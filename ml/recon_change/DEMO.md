# Running the demo

What runs where, and the order to start it in. Written on Sat 14 Nov for whoever holds the laptop.

## The pieces

| Piece | What it does | Where it runs |
| --- | --- | --- |
| Event API (`backend/`) | Sensor events, the answers to "Did I take my pills?", speech | Laptop, port 8000 |
| Vision relay (`ml/recon_change/live/relay.py`) | Position, arrow, places, reminders, lost items, the day log, the pages | Laptop, port 8090 |
| Vision worker (`live/worker.py`) | The models: the 3D model, the item finder, the spot check | University cluster, or this laptop |
| Android app (`android/`) | The glasses: a spoken question in, the answer on the display and spoken | Phone |
| Pill box sensor | UNO Q, or the simulator | Its own board, or the laptop |

The Android app talks to the event API only. It does not send the glasses' camera to the relay and does
not draw the arrow: the arrow, the reminders and the day page are on the relay's pages (`/hud`, `/places`,
`/dayview`), opened in a browser on the phone or the laptop.

## Start it

From the top of the repo, in Git Bash:

    bash ml/recon_change/run_demo.sh

This starts the event API and the relay, wired to each other, and makes `.env.demo` (ignored by Git) with
the two secrets the first time. The relay prints the link to its pages. The API token to type into the
Android app is the `API_TOKEN` line in `.env.demo`.

The models, one of:

- **University cluster** (needs the VPN): on the cluster, in `/scratch/$USER/junction-recon`, run
  `bash iridis/restart_workers.sh`. Jobs last two hours and a replacement is queued behind each.
- **This laptop** (no VPN, no cluster): `bash ml/recon_change/run_demo.sh --local /path/to/python`, where
  that Python has GPU PyTorch and VGGT. See "Models on the laptop" below.

## With the phone and the glasses

1. Connect the phone by USB and forward both ports: `adb reverse tcp:8000 tcp:8000` and
   `adb reverse tcp:8090 tcp:8090`.
2. Android app: enter `http://127.0.0.1:8000` and the API token, then Connect backend. Then the glasses
   steps in `android/README.md` ("At the venue: real glasses").
3. Ask through the glasses: "Did I take my pills?". The answer comes from the sensor events.
4. For a position, the relay needs photos. Open the relay's `/hud` link in Chrome on the phone at
   `http://127.0.0.1:8090/...` (the same link the relay printed, with 127.0.0.1) and press "Use this camera":
   a photo goes out when the phone has stopped moving and the view has changed. The arrow is on that
   page. "Where is the pill box?" asked through the glasses is then answered from the latest position.
5. A room first needs a baseline: open the relay's phone page (`/`), record 30 seconds of the room while
   turning to every side, then name places on `/places`.
6. The pill box: Ayman's board, or the simulator, sending `pillbox_open` to the event API. A reminder set
   on `/places` ends when that event arrives.

Without any phone: `/hud?...&demo=1` is a sample room that needs nothing else, and
`python live/simulate.py --key KEY --zone ZONE --photos PICTURES` plays recorded pictures through the
relay as if someone were wearing the glasses.

## Models on the laptop

One-off, about 7.5 GB of downloads into a separate environment (nothing in the system Python changes):

    python -m venv /path/to/venv-local
    /path/to/venv-local/Scripts/python -m pip install --index-url https://download.pytorch.org/whl/cu121 torch==2.3.1 torchvision==0.18.1
    /path/to/venv-local/Scripts/python -m pip install "numpy<2" opencv-python-headless pillow huggingface_hub einops safetensors psutil
    /path/to/venv-local/Scripts/python -m pip install --no-deps "git+https://github.com/facebookresearch/vggt.git"

The weights (5 GB) are fetched the first time the worker starts. On a graphics card with less than 10 GB
the weights are kept at half size, and `run_local.sh` uses fewer baseline photos per fix (5 instead of 12)
and no item finder. That is a weaker setting than the cluster's: see the notes in `HANDOFF.md` for what was
measured with it.

## What is not connected

- The glasses' own camera does not reach the relay. The phone's camera does, through `/hud`.
- The arrow is not drawn on the glasses' display, only the text answer.
- Reminders and "Where are my keys?" are answered by the relay's pages, not by the Android app, which asks
  the event API directly.
