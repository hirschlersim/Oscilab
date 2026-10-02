"""
Showcases: Oscilab with real data sources, each a command of its own.

    uv run oscilab-xbox            # an Xbox controller's sticks, triggers and buttons
    uv run oscilab-sensor-logger   # your phone's sensors, from the Sensor Logger app
    uv run oscilab-udp --demo      # samples over UDP, sending itself some to try it

Each module is also a short example of the API to start your own setup
from; `--help` lists a command's options. (`uv run oscilab-demo`, the demo
with synthetic signals, is `oscilab.demo`.)
"""
