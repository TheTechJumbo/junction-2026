use chrono::{SecondsFormat, Utc};
use clap::{Parser, ValueEnum};
use serde::Serialize;
use std::{process::ExitCode, thread, time::Duration};

#[derive(Clone, Copy, ValueEnum)]
enum Sensor {
    Pillbox,
    Door,
}

#[derive(Parser)]
#[command(about = "Send one clearly identified simulated sensor opening (no hardware required)")]
struct Args {
    #[arg(value_enum)]
    sensor: Sensor,
    #[arg(long, env = "UNOQ_BASE_URL", default_value = "http://127.0.0.1:8091")]
    base_url: String,
    #[arg(long, default_value = "kitchen")]
    zone: String,
    /// Optional stable ID for manual replay; must start with sim-.
    #[arg(long)]
    event_id: Option<String>,
    /// Optional original RFC3339 timestamp for manual replay.
    #[arg(long)]
    timestamp: Option<String>,
    /// Print the event without sending it or requiring a token.
    #[arg(long)]
    dry_run: bool,
    /// Total attempts, including the first request.
    #[arg(long, default_value_t = 3, value_parser = clap::value_parser!(u8).range(1..=5))]
    attempts: u8,
    #[arg(long, default_value_t = 5, value_parser = clap::value_parser!(u64).range(1..=30))]
    timeout_seconds: u64,
}

/// Wire format shared by the local UNO Q store and the backend.
/// Real hardware can later supply the trigger without changing this contract.
#[derive(Serialize)]
struct Event {
    id: String,
    r#type: &'static str,
    source: &'static str,
    ts: String,
    zone: String,
    object: &'static str,
    evidence_url: Option<String>,
    confidence: u8,
}

fn event(args: &Args) -> Result<Event, &'static str> {
    let id = args
        .event_id
        .clone()
        .unwrap_or_else(|| format!("sim-{}", uuid::Uuid::new_v4()));
    if !id.starts_with("sim-")
        || id.len() <= 4
        || id.len() > 128
        || !id
            .bytes()
            .all(|b| b.is_ascii_alphanumeric() || b == b'-' || b == b'_')
    {
        return Err("event ID must start with sim- and contain 5..128 ASCII letters, digits, underscores or hyphens");
    }
    if args.zone.trim().is_empty() || args.zone.chars().count() > 256 {
        return Err("zone must contain 1..256 characters");
    }
    let ts = match &args.timestamp {
        Some(ts) => chrono::DateTime::parse_from_rfc3339(ts)
            .map_err(|_| "timestamp must be RFC3339 with a timezone")?
            .with_timezone(&Utc)
            .to_rfc3339_opts(SecondsFormat::Millis, true),
        None => Utc::now().to_rfc3339_opts(SecondsFormat::Millis, true),
    };
    let (kind, object) = match args.sensor {
        Sensor::Pillbox => ("pillbox_open", "pillbox"),
        Sensor::Door => ("door_open", "door"),
    };
    Ok(Event {
        id,
        r#type: kind,
        source: "sensor",
        ts,
        zone: args.zone.clone(),
        object,
        evidence_url: None,
        confidence: 1,
    })
}

fn endpoint(base: &str) -> Result<String, &'static str> {
    let parsed = base.parse::<url::Url>().map_err(|_| "invalid base URL")?;
    if !matches!(parsed.scheme(), "http" | "https")
        || parsed.host_str().is_none()
        || !parsed.username().is_empty()
        || parsed.password().is_some()
        || parsed.query().is_some()
        || parsed.fragment().is_some()
    {
        return Err("base URL must be HTTP(S), without credentials, query or fragment");
    }
    Ok(format!("{}/events", base.trim_end_matches('/')))
}

fn send(args: &Args, payload: &Event, token: &str) -> Result<(), String> {
    let endpoint = endpoint(&args.base_url)?;
    if token.is_empty() || token.chars().any(char::is_control) {
        return Err("set UNOQ_TOKEN to a valid API bearer token".into());
    }
    let agent = ureq::AgentBuilder::new()
        .timeout(Duration::from_secs(args.timeout_seconds))
        .redirects(0)
        .build();
    // Serialize once: a retry must keep both ID and timestamp unchanged.
    let body = serde_json::to_string(payload).map_err(|_| "cannot encode event")?;
    for attempt in 1..=args.attempts {
        let failure = match agent
            .post(&endpoint)
            .set("Authorization", &format!("Bearer {token}"))
            .set("Content-Type", "application/json")
            .send_string(&body)
        {
            Ok(response) if (200..300).contains(&response.status()) => return Ok(()),
            Ok(response) => (false, format!("HTTP {}", response.status())),
            Err(ureq::Error::Status(status, _)) => (
                status == 408 || status == 429 || status >= 500,
                format!("HTTP {status}"),
            ),
            Err(ureq::Error::Transport(_)) => (true, "transport failure".into()),
        };
        // Never print URLs, bearer tokens, server bodies or household payloads.
        if !failure.0 || attempt == args.attempts {
            return Err(format!(
                "{} after {attempt} attempt(s); delivery may be uncertain on transport failure",
                failure.1
            ));
        }
        eprintln!(
            "Retrying simulated event ({attempt}/{}): {}",
            args.attempts, failure.1
        );
        thread::sleep(Duration::from_millis(250 * (1 << (attempt - 1))));
    }
    unreachable!()
}

fn run(args: Args) -> Result<(), String> {
    let payload = event(&args)?;
    if args.dry_run {
        println!(
            "{}",
            serde_json::to_string(&payload).map_err(|_| "cannot encode event")?
        );
    } else {
        let token = std::env::var("UNOQ_TOKEN")
            .map_err(|_| "set UNOQ_TOKEN to the target API bearer token")?;
        send(&args, &payload, &token)?;
        println!("Accepted SIMULATED event {} at {}", payload.id, payload.ts);
    }
    Ok(())
}

fn main() -> ExitCode {
    match run(Args::parse()) {
        Ok(()) => ExitCode::SUCCESS,
        Err(error) => {
            eprintln!("sensor-sim: {error}");
            ExitCode::FAILURE
        }
    }
}
