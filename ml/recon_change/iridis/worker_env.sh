# Settings for the live workers, sourced before sbatch (sbatch passes the environment on to the job).
# Item finder: packages and weights in items/, a limit per item, and a second opinion from CLIP on weak
# boxes (eval/ITEMS_RESULTS.md, "Finding more").
export RECON_ITEMS_ENV=items
export RECON_ITEMS_LIMITS="keys=0.425,glasses=0.225,wallet=0.275,phone=0.4"
export RECON_ITEMS_LOOK_FROM=0.25
export RECON_ITEMS_JUDGE=openai/clip-vit-large-patch14
export RECON_ITEMS_JUDGE_LIMITS="keys=0.7,glasses=0.1,wallet=0.1,phone=0.35,remote control=0.5"
export RECON_ITEMS_CANDIDATES=0.25
