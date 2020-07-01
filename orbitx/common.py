# -*- coding: utf-8 -*-
"""Common code and class interfaces."""

import atexit
import logging
import sys
from io import StringIO
from pathlib import Path
from typing import NamedTuple, Optional

import google.protobuf.json_format
import numpy
import pytz
import vpython

from orbitx import data_structures
from orbitx import orbitx_pb2 as protos

# Frequently-used entity names are here as constants. You can use string
# literals instead, but that's more prone to mipsellings.
HABITAT = 'Habitat'
AYSE = 'AYSE'
SUN = 'Sun'
EARTH = 'Earth'
MODULE = 'Module'
SUN = 'Sun'
OCESS = 'OCESS'

TIME_BETWEEN_NETWORK_UPDATES = 1.0

FRAMERATE = 60


class TimeAcc(NamedTuple):
    """Represents a single value of time acc."""
    value: int
    desc: str
    # The acceleration above which this time acc starts to be too inaccurate.
    accurate_bound: float


# If you change the 'Pause' element of this list, change the corresponding
# JS code in flight_gui_footer.html also.
TIME_ACCS = [
    TimeAcc(value=0, desc='Pause', accurate_bound=10000),
    TimeAcc(value=1, desc='1×', accurate_bound=1000),
    TimeAcc(value=5, desc='5×', accurate_bound=12),
    TimeAcc(value=10, desc='10×', accurate_bound=9),
    TimeAcc(value=50, desc='50×', accurate_bound=7),
    TimeAcc(value=100, desc='100×', accurate_bound=5),
    TimeAcc(value=1_000, desc='1,000×', accurate_bound=3),
    TimeAcc(value=10_000, desc='10,000×', accurate_bound=1),
    TimeAcc(value=100_000, desc='100,000×', accurate_bound=0.1)
]

# ---------------- Graphics-related constants ---------------
DEFAULT_CENTRE = HABITAT
DEFAULT_REFERENCE = EARTH
DEFAULT_TARGET = AYSE

DEFAULT_UP = vpython.vector(0, 0.1, 1)
DEFAULT_FORWARD = vpython.vector(0, 0, -1)

TIMEZONE = pytz.timezone('Canada/Eastern')

# ---------------- Physics-related constants ----------------
G = 6.674e-11

MIN_THROTTLE = -1.00  # -100%
MAX_THROTTLE = 1.00  # 100%

# The max speed at which the autopilot will spin the craft.
AUTOPILOT_SPEED = numpy.radians(20)

# The margin on either side of the target heading that the autopilot will slow
# down its adjustments.
AUTOPILOT_FINE_CONTROL_RADIUS = numpy.radians(5)

UNDOCK_PUSH = 0.5  # Undocking gives a 0.5 m/s push


class Spacecraft(NamedTuple):
    """Represents the capabilities of different craft."""
    fuel_cons: float  # Fuel consumption in kg/s at 100% engines.
    thrust: float  # Thrust in N at 100% engines.
    hull_strength: float  # Max m/s impact the craft can take.


# These numbers taken from orbit5vm.bas.
craft_capabilities = {
    HABITAT: Spacecraft(fuel_cons=4.824, thrust=4375000, hull_strength=50),
    AYSE: Spacecraft(fuel_cons=17.55, thrust=6.4e9, hull_strength=100)
}

SRB_THRUST = 13125000

# Rotating the craft changes the spin by this amount per button press.
SPIN_CHANGE = numpy.radians(5)  # 5 degrees per second.
FINE_SPIN_CHANGE = numpy.radians(0.5)  # Half a degree per second.

HAB_DRAG_PROFILE = 0.0002
PARACHUTE_DRAG_PROFILE = 0.02

# The thrust-weight ratio required for liftoff. Realistically, the TWR only has
# to be greater than 1 to lift off, but we want to make sure there aren't any
# possible collisions that will set the engines to 0 again.
LAUNCH_TWR = 1.05

# These special values mean that the SRBs are full but haven't been used, and
# that the SRBs have been fully used, respectively.
SRB_FULL = -1
SRB_EMPTY = -2
SRB_BURNTIME = 120  # 120s of burntime.

# ---------- Other runtime constants ----------
PERF_FILE = 'flamegraph-data.log'

if getattr(sys, 'frozen', False):
    # We're running from a PyInstaller exe, use the path of the exe
    PROGRAM_PATH = Path(sys.executable).parent
elif sys.path[0] == '':
    # We're running from a Python REPL. For information on what sys.path[0]
    # means, read https://docs.python.org/3/library/sys.html#sys.path
    # note path[0] == '' means Python is running as an interpreter.
    PROGRAM_PATH = Path.cwd()
else:
    PROGRAM_PATH = Path(sys.path[0])


def format_num(num: Optional[float], unit: str,
               *, decimals: Optional[int] = None) -> str:
    """This should be refactored with the Menu class after symposium."""
    # This return string will be at most 10 characters
    if num is None:
        return ''
    return '{:,.5g}'.format(round(num, ndigits=decimals)) + unit


def savefile(name: str) -> Path:
    return PROGRAM_PATH / 'data' / 'saves' / name


def load_savefile(file: Path) -> 'data_structures.PhysicsState':
    """Loads the physics state represented by the input file.
    If the input file is an OrbitX-style .json file, simply loads it.
    If the input file is an OrbitV-style .rnd file, tries to interpret it
    and also loads the adjacent STARSr file to produce a PhysicsState."""

    # We shouldn't import orbitv_file_interface at the top of common.py, since
    # common.py is imported by lots of modules and shouldn't circularly depend
    # on anything.
    from orbitx import orbitv_file_interface
    physics_state: data_structures.PhysicsState
    logging.getLogger().info(f'Loading savefile {file.resolve()}')

    assert isinstance(file, Path)
    if file.suffix.lower() == '.rnd':
        physics_state = \
            orbitv_file_interface.clone_orbitv_state(file)

    else:
        if file.suffix.lower() != '.json':
            logging.getLogger().warning(
                f'{file} is not a .json file, trying to load it anyways.')

        with open(file, 'r') as f:
            data = f.read()
        read_state = protos.PhysicalState()
        google.protobuf.json_format.Parse(data, read_state)
        physics_state = data_structures.PhysicsState(None, read_state)

    if physics_state.time_acc == 0:
        physics_state.time_acc = 1
    if physics_state.reference == '':
        physics_state.reference = DEFAULT_REFERENCE
    if physics_state.target == '':
        physics_state.target = DEFAULT_TARGET
    if physics_state.srb_time == 0:
        physics_state.srb_time = SRB_FULL
    return physics_state


def write_savefile(state: 'data_structures.PhysicsState', file: Path):
    """Writes state to the specified savefile path (use common.savefile to get
    a savefile path in data/saves/). Returns a possibly-different path that it
    was saved under."""
    if file.suffix.lower() != '.json':
        # Ensure a .json suffix.
        file = file.parent / (file.name + '.json')
    logging.getLogger().info(f'Saving to savefile {file.resolve()}')
    with open(file, 'w') as outfile:
        outfile.write(
            google.protobuf.json_format.MessageToJson(state.as_proto()))
    return file


def start_flamegraphing():
    # TODO: codify my workflow for profiling graphics vs simulation code.
    # Basically, profiling graphics is done by using dev tools of whatever
    # browser (in Firefox, F12 > Performance > Start Recording Performance)
    # And profiling python simulation code is done by running this function,
    # then processing PERF_FILE with https://github.com/brendangregg/FlameGraph
    # The command is, without appropriate paths, is:
    # dos2unix PERF_FILE && flamegraph.pl PERF_FILE > orbitx-perf.svg
    # Where flamegraph.pl is from that brendangregg repo.
    import flamegraph
    flamegraph.start_profile_thread(
        fd=open(PERF_FILE, 'w'),
        filter=r'(simthread|MainThread)'
    )


def start_profiling():
    # This will show the performance impact of each function.
    # If you want to see the performance impact of each _line_ in a function,
    # pip install line_profiler
    # and add @profile annotations to functions of interest, then run kernprof
    # as described in the line_profiler package.
    import yappi
    yappi.set_clock_type('cpu')
    yappi.start()
    atexit.register(_dump_profiling_stats)


def _dump_profiling_stats():
    import yappi
    # To find out what functions have the biggest impact on performance,
    # sort by 'tsub' or 'ttot'. Docs are here:
    # https://github.com/sumerc/yappi/blob/master/doc/api.md#yfuncstat

    # Only print the first bunch of lines of yappi output.
    yappi.stop()
    yappi_output = StringIO()
    yappi.get_func_stats().sort('tsub').print_all(out=yappi_output)
    for line in yappi_output.getvalue().split('\n')[0:30]:
        print(line)


def remove_vpython_css():
    """Remove the inline per-element styling that vpython adds."""
    vpython.canvas.get_selected().append_to_caption("""<script>
        for (const element of document.querySelectorAll(
                "div, input, select, button, span")) {
            float_backup = element.style.float;
            element.removeAttribute('style');
            element.style.float = float_backup;
        }
    </script>""")


def include_vpython_footer_file(footer_path: Path):
    """Append the contents of a file to the vpython caption.
    Useful for including HTML or CSS files."""
    with open(footer_path) as footer:
        vpython.canvas.get_selected().append_to_caption(footer.read())
