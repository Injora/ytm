"""PixelFM — a retro-futuristic terminal music player."""

import logging

__version__ = "2.4.0"

# Library default: no output unless the app configures a log file. This also
# stops Python's last-resort handler from printing warnings over the TUI.
logging.getLogger("pixelfm").addHandler(logging.NullHandler())
