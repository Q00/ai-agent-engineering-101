"""User-requested adjustment, applied once per final rating path."""


def adjusted_stars(original):
    return max(1,min(5,int(original)))
