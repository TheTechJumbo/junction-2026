#!/bin/sh
# Invoke on UNO Q. Each invocation is isolated from the storage service.
set -eu
export XDG_RUNTIME_DIR=/run/user/$(id -u)
export HF_HUB_OFFLINE=1
export OMP_NUM_THREADS=2
export OPENBLAS_NUM_THREADS=2
export PYTHONPATH=/home/arduino/junction-vggt
umask 077
exec /usr/bin/python3 /home/arduino/junction/unoq/quant/supervise.py "$@"
