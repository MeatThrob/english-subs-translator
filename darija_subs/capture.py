"""Screen-region capture via Quartz.

Captures raw pixels from a rectangle in global display coordinates
(origin = top-left of the primary display, y grows downward). Because it
reads the screen, it works over ANY app or website that draws subtitles:
browsers, VLC, IINA, native players, anything.

Requires the Screen Recording permission for the process that runs it
(System Settings > Privacy & Security > Screen Recording).
"""

import Quartz


def has_screen_permission() -> bool:
    try:
        return bool(Quartz.CGPreflightScreenCaptureAccess())
    except AttributeError:
        # Pre-10.15 fallback: assume yes
        return True


def request_screen_permission() -> bool:
    """Triggers the system prompt (first time only). Returns current state."""
    try:
        return bool(Quartz.CGRequestScreenCaptureAccess())
    except AttributeError:
        return True


def grab_region(x: float, y: float, w: float, h: float):
    """Return a CGImageRef of the given global-coordinate rect, or None.

    The overlay window sets sharingType=NSWindowSharingNone, so it is
    invisible to this capture even if it overlaps the region.
    """
    rect = Quartz.CGRectMake(x, y, w, h)
    img = Quartz.CGWindowListCreateImage(
        rect,
        Quartz.kCGWindowListOptionOnScreenOnly,
        Quartz.kCGNullWindowID,
        Quartz.kCGWindowImageBestResolution,
    )
    if img is not None and Quartz.CGImageGetWidth(img) > 1:
        return img
    return None
