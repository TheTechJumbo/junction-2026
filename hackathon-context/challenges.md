# Junction 2026 challenges: the facts

Source: `raw/Junction 2026 challenges.html`, saved from junction2026.com/challenges.
Everything below comes from that file. Nothing is added. Where the file says nothing, this says so.
Passwords and API keys in the file are left out here; look them up in the raw file.

There are **38 challenges**: 19 main challenges in six groups, and 19 tech challenges.

## General rules (what the file says)

- The file has a heading "**Tech challenges stack on top of a main challenge**" before the tech list.
- Several tech challenges repeat this: Aiven ("You can enter this tech challenge alongside your main
  challenge"), Modal ("you compete in it on top of your main challenge ... The same project can enter
  other tech challenges too"), Arduino UNO Q ("open to all teams, so you can combine it with any other
  Junction challenge"), Codex ("You can participate alongside any other Junction challenge"),
  Nemotron ("Applies to: Any track"), CRACI ("even a project made mainly for another challenge").
- The file does **not** give a maximum number of tech challenges per team.
- The file does **not** say in so many words "one main challenge per team". It implies it ("your main
  challenge", singular).
- Judging: the only general statement is in the Arduino entries: you submit "on the Junction
  platform, where peer review and judging take place". The file does **not** describe "partners pick
  winners, then peer review picks a top 5, then a grand jury" (that comes from the brainstorm, not
  from this file).
- The file has **no overall submission deadline**. Dates and times it does give:
  - Bahn Spread: ranking rounds from Fri 19:30; final Sun 08:00; registration closes 07:30.
  - AstraZeneca: briefing Fri 20:00; judging Sun 15 Nov at 14:00.
  - Aalto Humanoid: top ~5 run on the real robot on Sunday.
  - Meta: best teams may be invited to an AI forum on Wed 18 Nov 2026.
  - Workshops: Cala Sat 11:00; Intel hands-on Sat 12:00; Hostinger talk Sat 13:00; Verda Fri 20:30;
    Google DeepMind "Build with Google DeepMind" Friday.
- Extra submission steps, by challenge (all facts from the file):
  - Arduino (both): also publish on Arduino Project Hub with the tag "Junction Finland 2026", or
    you are not eligible for prizes.
  - Cloudinary: fill in the form at cld.media/hackathon-survey.
  - Context.dev tech: GitHub repo, demo, short note on which endpoints and why.
  - Oxylabs: same repo and demo as the main challenge, plus a note on which endpoints and why.
  - Nemotron: a short text in the submission form (model, what it does, why it matters, how you
    improved its output).
  - Verda: document your process, post a summary link in their Discord channel.
  - Atech: post a summary link in their Discord; live demo on the hardware; public GitHub repo or
    exported Atech project; about a 2-minute video or live demo.
  - Gemini: participants must be 18 or older to take part and use the Google accounts.
  - Intel: agree to the Challenge Terms (Google Drive link in the file).

---

# Main challenges

## Agentic AI

### 1. Valio Aimo: Trusted Trade Agents
- **Ask:** an agent-facing interface to a foodservice wholesaler's mock APIs: product lookup,
  ordering, human-approval guardrails and an audit trail. Core: product, price and availability
  lookup; request price and availability, then place an order (fixed prices, no negotiation);
  permission and human-approval management. Optional: delivery time and sustainability comparison,
  substitution and exceptions, traceability.
- **Criteria:** Working end-to-end demo 30% (full request, lookup, order or approval flow);
  Solving the problem and user need 20%; Autonomy and controls 20%; Robustness to exception
  situations 15%; Openness and credible path toward production 15%.
- **Prizes:** 1st 4 000€, 2nd 2 000€, 3rd 1 000€.
- **Tech:** synthetic catalog and mock APIs on the resource portal junction26.valioaimo.fi
  (password and API key in the file). Preferred: agent frameworks, open interop standards,
  optional verifiable credentials. x402 or Nevermined are context, not required.
- **Rules:** synthetic data only.

### 2. AfterQuery: Break the Agent
- **Ask:** an RL environment where a frontier agent measurably underperforms your reference run,
  with a reward that resists exploits.
- **Criteria:** The gap 30; Meaningful difficulty 30; Reward integrity 25; Craft 15 (points).
- **Prizes:** 1st 3,000€ + work trial at AfterQuery + merch; 2nd 1,500€ + merch; 3rd 500€.
- **Tech:** Harbor + OpenCode harness over MCP, scoring skeleton, model credits per team.
- **Rules:** 10-minute demo. Judges rerun: your reference run, three agent runs, a do-nothing
  baseline and a stop-early baseline, then spend 15 minutes looking for reward exploits. Must run
  from your README with one command. Tiebreaker: a smaller model improving after training on the
  environment.

### 3. Exa: The Internet Butler / Your Personal Assistant
- **Ask:** a personal agent that researches the live web and gets tedious life tasks done,
  showing its sources. Demo on a real case.
- **Criteria:** Use of Exa 20%; Usefulness 20%; Clarity (of sources) 20%; Originality 20%;
  Working Implementation 20% (live queries, including the judges' own).
- **Prizes:** 1st $2,500 + $5,000 Exa credits + hoodie each + Exa TRMNL dashboard; 2nd $1500 +
  $3,000 credits + hoodie each + macropad; 3rd $500 + $1k credits + hoodie each. Every participant
  gets $50 credits.
- **Tech:** Exa Search and Contents APIs, Websets, Monitors, MCP server, SDKs. Code EXA50JUNCTION.

### 4. Hostinger: Decode the Noise
- **Ask:** detect propaganda, bot networks or malicious traffic, with honest false-positive
  handling.
- **Criteria:** Detection quality (accuracy, false-positive handling, explainability) 40%;
  Technical execution and live deployment on Hostinger 25%; Originality and real-world impact 20%;
  Demo clarity 15%.
- **Prizes:** 1st 2000 €, 2nd 1000 €, 3rd 500 €; all three also get a year of hosting, a domain,
  swag and a social spotlight.
- **Tech:** free KVM 2 VPS per team, AI Router credits, AI Builder, Hostinger MCP connector.
- **Rules:** must be deployed and running live on Hostinger by the end, with a public URL.

### 5. Tieto: Earned Trust in AI Code
- **Ask:** an AI code-review concept where a named human stays accountable, shown on agent-written
  PR branches (MessagePack-CSharp repo). Concept counts more than implementation.
- **Criteria:** Clarity 30%; Practicality 25%; Measurement 25%; Evidence 20%.
- **Prizes:** 1st €2,000, 2nd €1,000, 3rd €500.
- **Deliverables:** concept card; live walkthrough; lessons log and a demo video or presentation.
  Synthetic or cleared data only.

### 6. Bahn Express: Spread
- **Ask:** an agent that runs a car-transport dispatch company for a simulated week: quote dealers,
  price driver gigs, maximise margin with at least 90% on time.
- **Prizes:** 1st 2000€, 2nd 1000€, 3rd 500€.
- **Rules:** join at spread.bahnexpress.com/join (code in the file). Final Sun 08:00, registration
  closes 07:30. The 10 teams with the highest margin present (5 to 10 slides or a short video);
  the jury then judges elegance, clarity, creativity and robustness.

### 7. Cala: Make Unicorns Real
- **Ask:** a product for founders or investors on Cala's sourced knowledge graph.
- **Criteria:** Real need 25%; Execution 25%; Originality 25%; Use of Cala 25%.
- **Prizes:** 1st "Cala Comet" (one hackathon per month for three months, travel covered, about
  €5,000 for a team of four) + €1,000 Cala credits + merch; runner-up "500€ + Cala Merch" in the
  card, "€500 in Cala credits and Cala merch" in the full brief.
- **Tech:** Cala API or MCP; code MAKEUNICORNSREAL.

## Hardware

### 8. Ignite Next x Intel: The Intel Physical AI Challenge
- **Ask:** an SO-101 arm picks Duplo bricks, inspects them with an Anomalib model and sorts good
  from defective, with a fine-tuned VLA policy running with OpenVINO on a Core Ultra laptop.
- **Criteria:** Task success 30%; Intel technology integration 30%; Dataset and pipeline quality
  20%; System efficiency 20%.
- **Prizes:** 1st €5,000, 2nd €3,000, 3rd €2,000.
- **Tech:** 25 stations, one per team, in the Hardware Lab. Must agree to the Challenge Terms.

### 9. Rokoko: The Robot Teleoperation Challenge
- **Ask:** control robot hands, arms or a humanoid with Rokoko mocap suits and gloves, physically
  or in simulation.
- **Criteria:** Technical execution 35%; Originality and creativity 25%; Usefulness and potential
  impact 25%; Demonstration of human-controlled robotics 15%.
- **Prizes:** 1st Smartgloves II + SDK licence + blog post (€4,990); 2nd Smartsuit Pro II
  (€2,295); 3rd Smartgloves I + merch (€1,095).

### 10. Aalto University: Aaltonomous Humanoid
- **Ask:** make a Unitree G1 sort coloured blocks into boxes on its own; develop in Isaac Sim.
- **Criteria:** Real-world result 40%; Generality 25%; Originality 20%; Presentation 15%.
- **Prizes:** winning team 2,000€ + a visit to the Aalto robotics lab; top ~5 (by simulation)
  run live on the real G1 on Sunday.
- **Rules:** submit a Docker image (policy server); must pass the safety check in simulation.
  RTX GPU with at least 24 GB.

### 11. Arduino: The Green European Renaissance
- **Ask:** Edge AI on the Arduino UNO Q for local green or social problems. Listed directions
  include "supporting elderly people's independence".
- **Criteria:** Technical Implementation and Edge AI 30%; Environmental and Social Impact 30%;
  Creativity and Innovation 20%; Working Prototype and Demo 20%.
- **Prizes:** 1st a Ventuno Q per team member; 2nd UNO Q 4G + Plug and Make Kit each; 3rd Plug and
  Make Kit each.
- **Rules:** must also publish on Arduino Project Hub with tag "Junction Finland 2026" to be
  eligible. Hardware from the Arduino Hardware Lab, 5th floor.

### 12. Meta: AI Glasses for Everyone, Everyday
- **Ask:** "Build the best everyday use case using AI glasses from Meta, showing how a first-person
  field of view enables new or better interactions in everyday scenarios." Web app or Device Access
  Toolkit (DAT) app. Display and audio-first count equally.
- **What they want to see:** a clear problem and audience; a functional prototype **and a deck**;
  "a clear wearable benefit and a focused, plausible path to adoption or a pilot"; a prototype that
  "handles permissions, errors, performance trade-offs, connectivity, and device state changes";
  interactions "intuitive and inclusive for people with all abilities, with appropriate consent and
  safe data practices". Value "beyond basic mirroring".
- **Criteria:** Creativity and use case 25%; Technical execution 25%; Real-world value and
  feasibility 25%; User experience: input, accessibility and trust 25%.
- **Prizes:** best overall: a pair of Ray-Ban Meta glasses for each member. Best teams may be
  invited to Meta's AI forum on Wed 18 Nov 2026.
- **Resources:** 10 Meta Ray-Ban Display and 10 RBM Gen 2, shared, for use during the event;
  mentors for web apps and DAT (iOS, Android) at the Meta booth; contact Amanda Le.

## Healthcare

### 13. AstraZeneca: Equal Care Across Finland
- **Ask:** find where Finnish cancer care is unequal, locate the gap in the pathway, and recommend
  concrete actions, using public data. Must cover at least one Goal 3 indicator of the national
  cancer strategy; working demo required (slides alone are not enough).
- **Criteria:** Actionability 30%; Insight 25%; Data quality and transparency 20%; Clarity 10%;
  Execution 15%.
- **Prizes:** 1st 1000€, 2nd 750€, 3rd 250€. Judging Sun 15 Nov, 14:00.

### 14. Aalto University: Beyond the Clinic
- **Ask:** help people stay well at home after TMS depression treatment: sense, intervene, learn
  per person.
- **Criteria:** Usefulness 25%; Ease of use 25%; Smart sensing 25%; Creativity 25%.
- **Prizes:** to be announced.

## AI Infrastructure

### 15. Stoa: Make Blueprints Talk
- **Ask:** answer natural-language questions about construction drawings, showing where in the
  drawing each answer comes from.
- **Criteria:** Accuracy 25%; Visualising reasoning so a human can verify 25%; Creativity 25%;
  Low inference cost 25%.
- **Prizes:** 1st 1000€ + work trial at Stoa; 2nd 750€; 3rd 250€. Dataset 0.5 GB preview, 10 GB full.

## Data

### 16. Context.dev: The Web Is Your Real-Time Database
- **Ask:** a product that reacts to live web data (prices, news, company pages, change over time).
- **Criteria:** Real-world impact 20%; Creative use of live web data 20%; Completeness of demo
  20%; Technical execution 20%; Real-timeness 20%.
- **Prizes:** 1st €2,000 + swag; 2nd €500 + swag; 3rd swag. 50k credits per team (code JUNCTION26).

### 17. City of Espoo: Rethinking Recruitment
- **Ask:** better matchmaking between companies and talent at city recruitment events.
- **Criteria:** Impact and Relevance 45%; Innovation and User Experience 35%; Responsible and
  Feasible Execution 20%.
- **Prizes:** 1st 1500€, 2nd 1000€, 3rd 500€.
- **Rules:** synthetic data; no automated decisions about people without a human; EU AI Act.

### 18. MariaDB: Wikipedia Through Time
- **Ask:** Wikipedia tools on MariaDB: RAG with vector search and/or time travel with
  system-versioned tables, over Finnish history articles.
- **Criteria:** Effective and creative use of MariaDB 20%; Implementation 20%; Usefulness 20%;
  Design and demo quality 20%; Originality 20%; Bonus 0% (extra credit for SVT or both).
- **Prizes:** 1st €1,000 + €1,000 UpCloud credits + MariaDB certification each; 2nd €500; 3rd €250.

## FinTech

### 19. S-Pankki: Personal Economic Trainer
- **Ask:** a mobile AI coach that lets people chat with their own bank transactions, with a
  financial-literacy angle.
- **Criteria:** Impact on financial literacy 20%; Originality of AI integration 20%; Clarity of
  the mobile UX 20%; Feasibility 20%; Potential to integrate into S-mobiili 20%.
- **Prizes:** 1st 2000€, 2nd 1000€, 3rd 500€ (Airbnb gift cards).

---

# Tech challenges (stack on top of a main challenge)

### 20. Aiven: Best Use of Aiven
- **Ask:** use at least one Aiven service as a meaningful part of your project and show what it
  enables. Examples: Aiven Runtime hosting, PostgreSQL, Valkey, Kafka, ClickHouse, OpenSearch.
  "A well-executed use of one service can be just as impressive as a solution combining several."
- **Criteria:** Meaningful use of Aiven technology 40%; Technical implementation 30%; Impact on the
  overall solution 20%; Creativity 10%.
- **Prizes:** 1st 1000€, 2nd 750€, 3rd 500€. $50 free credits on sign-up.

### 21. Arduino: Best use of Arduino UNO Q
- **Ask:** use the UNO Q and Modulino sensors in your project, whatever challenge you are solving.
  "Run AI models directly on the device, sense the real world and make things move, light up or
  react." "The best projects combine a working physical prototype with a smart use of on-device AI."
- **Criteria:** Use of Arduino hardware and Edge AI 50%; Working prototype and demo 30%;
  Creativity 20%.
- **Prizes:** an Arduino UNO Q and a Plug and Make Kit for each member of the winning team.
- **Hardware listed:** UNO Q (2 GB RAM, 16 GB eMMC), USB-C cable, USB-C hub, 45 W supply, Arduino
  USB Camera, Modulino Buzzer, Knob and Thermo. Software: Arduino App Lab.
- **Rules:** publish on Arduino Project Hub with tag "Junction Finland 2026" as well as on the
  Junction platform, or you are not eligible.

### 22. Atech: Atech Real World Challenge
- **Ask:** a physical device that solves a real problem for a real person, using Atech modules
  (snap-together ESP32 modules: distance, IMU, temperature and humidity, air quality, microphone,
  speakers, motors) as its body and AI as its brain. Inspiration includes "fall detection, posture
  coaching, smart reminders for people living alone".
- **Criteria:** Real-world validation of usefulness 25%; Real-world impact 25%; Use of Atech 25%;
  Working demo and storytelling 25%; Bonus 0% (extra credit for several modules or AI/APIs).
- **Prizes:** best overall: a Pro Atech kit; runners-up: Atech kits and swag; top teams invited to
  show on Atech's channels and visit Copenhagen.
- **Rules:** Atech hardware required in the demo; share a summary link in their Discord; live
  demo on the hardware; public GitHub repo or exported project; about 2-minute video or live demo.
  Kits are limited (2 to 4 per team), first come first served at the booth; code JUNCTION.

### 23. Cloudinary: Best Use of Cloudinary
- **Ask:** use one of the four tools on cld.media/hackathons to make an image or video experience
  more engaging and intelligent. Cloudinary must play "a meaningful role in the solution, not just
  a CDN".
- **Criteria:** Innovation 30%; Technical Implementation 20%; UX / Design 30%; Impact / Usefulness 20%.
- **Prizes:** 1st $500 Amazon gift cards per team member.
- **Rules:** you must enter the project at cld.media/hackathon-survey.

### 24. Context.dev: Best Use of Context.dev: Fully Structured Web
- **Ask:** call at least one Context.dev endpoint in the core flow; the data must visibly drive
  something in the demo.
- **Criteria:** Creative use of Context.dev 40%; How central live web data is 30%; Technical
  execution 30%.
- **Prizes:** 1st €750 + swag; 2nd €250 + swag; 3rd swag.
- **Rules:** GitHub repo, demo, short note on endpoints used and why.

### 25. CRACI: Best use of CRACI
- **Ask:** run your project's CI/CD on CRACI (`runs-on: craci`) and use its SBOMs, AI fixes,
  provenance, network traces and API. "Do not just add runs-on: Craci to a project and stop there."
- **Criteria:** Technical depth 50%; How creatively you use CRACI's features 50%.
- **Prizes:** 1st LEGO set each; 2nd 24-pack of Red Bull each; 3rd invite to the CRACI x OpenAI
  lunch during Slush; top 5: 50,000 build minutes per member and a chance to interview.
- **Rules:** running the project's CI on CRACI is required. 1000€ credits per team.

### 26. Google DeepMind: Best Use of Gemini API
- **Ask:** "a meaningful, innovative, and technically impressive use of Gemini". "Projects that go
  beyond simply adding an LLM to an application." The submission must explain the problem, how
  Gemini is integrated, what is innovative, and why Gemini is important.
- **Criteria:** none listed in the file.
- **Prizes:** 1st 4000€ + $1,000 Google Cloud credits per member; 2nd 2000€; 3rd 1000€.
- **Rules:** credits for up to 400 teams; participants must be 18 or older.

### 27. Google DeepMind: Best Use of Gemma
- **Ask:** "the most innovative and impactful use of Google's Gemma open models".
- **Criteria:** none listed (description mentions creative applications, strong technical
  execution, open models solving real-world problems).
- **Prizes:** €1,000 per team for the top 3, no ranking.
- **Access:** Featherless AI, Modal, Verda, Google AI Studio, Hugging Face.

### 28. MLH: Best Use of ElevenLabs
- **Ask:** natural, human-sounding audio with ElevenLabs in any project.
- **Criteria:** none listed. **Prize:** wireless earbuds. Code at mlh.link/junction-26.

### 29. MLH: Best Use of Entire
- **Ask:** use as many of Entire's Sessions, Graph, Brain (and Trails) as possible in your workflow;
  show measurable token efficiency.
- **Criteria:** none listed. **Prize:** mini portable projector.

### 30. MLH: Best Use of MongoDB Atlas
- **Ask:** build using MongoDB Atlas. **Criteria:** none listed. **Prize:** M5Stack IoT Kit each.

### 31. MLH: Best Use of Solana
- **Ask:** apps on Solana. **Criteria:** none listed. **Prize:** Unruggable hardware wallets each.

### 32. MLH: Best Use of Tiger Data
- **Ask:** PostgreSQL extended for real-time and time-series data (continuous aggregates,
  compression). **Criteria:** none listed. **Prize:** Stream Deck Mini.

### 33. MLH: Best Use of Vultr
- **Ask:** use Vultr cloud compute or GPUs. **Criteria:** none listed. **Prize:** M5Stack mini
  programmable monitor.

### 34. Modal: Best use of Modal
- **Ask:** use Modal to power the project you are already building: serve an open model on a GPU,
  Sandboxes, fine-tuning, batch jobs. "The most ambitious working AI system built on Modal."
- **Criteria:** Effective use of Modal 40%; Technical ambition 25%; Impact 20%; Demo 15% ("a live,
  deployed app the judges can actually use").
- **Prizes:** 1st $2000 cash + $3000 Modal credits. $100 credits per participant (code in file).

### 35. NVIDIA: Best Use of Nemotron
- **Ask:** use NVIDIA Nemotron 3.5 Super VL in the working solution, central to its value.
- **Criteria:** Meaningful Nemotron Usage 25%; Technical Execution 25%; Quality of AI Output 25%;
  Creativity and Differentiation 25%.
- **Prizes:** two best teams: one NVIDIA RTX 5080 each.
- **Rules:** submit a short text naming the model, what it does, why it matters, how you improved
  its outputs.

### 36. OpenAI: Best Use of Codex
- **Ask:** use Codex in your development process; show "how you've meaningfully used Codex in
  building your solution". Open to any challenge.
- **Criteria:** none listed.
- **Prizes:** 1st $10 000 API credits; 2nd $5000; 3rd $2500. $100 credits per participant.

### 37. Oxylabs: Best agentic usecase of Oxylabs Web API
- **Ask:** use the Oxylabs Web API (/search, /scrape) at runtime so the agent acts on what is true
  on the web now, and shows its sources.
- **Criteria:** Use of the Web API 30%; Real-world value 25%; Trust 20%; Execution 15%;
  Originality 10%.
- **Prizes:** €1,000 Apple voucher + $500 Oxylabs credits per member.
- **Rules:** at least one live call at runtime; show source URLs; same repo and demo as the main
  challenge plus a note on endpoints. Access needs the $500 Flexible plan with code JUNCTION2026
  (full discount for the first month; cancel afterwards).

### 38. Verda: Verda Compute Track
- **Ask:** "Pick any challenge at Junction and use Verda products to build something you couldn't
  build on a laptop. Then show us how far it goes." One listed example: "Keep sensitive data in
  Europe. Run open-weight models ... on infrastructure you control." Products: Inference API, GPU
  instances and Instant Clusters, CPU instances, auto-scaling containers, batch jobs, a Nemotron
  endpoint. European data centres, 100% renewable energy.
- **Criteria:** Ambition 25%; Fit 25%; Working prototype 25% ("does it run, and is the process
  documented?"); Path To Scale 25%.
- **Prizes:** 1st $10,000 cloud credits + hoodies; 2nd $2,500 credits; 3rd $2,500 credits.
- **Rules:** document your process; post a summary link in their Discord channel. Crypto mining
  gets you banned. Free credits per team (QR code on the table or verda.com/blog/junction-2026).
