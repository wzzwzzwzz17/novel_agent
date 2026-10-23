class NovelRefError(Exception):
    """Base skill error."""


class CaptchaError(NovelRefError):
    pass


class ParseError(NovelRefError):
    pass


class RobotsDisallowError(NovelRefError):
    pass


class NoCandidateSiteError(NovelRefError):
    pass


class CircuitOpenError(NovelRefError):
    pass
