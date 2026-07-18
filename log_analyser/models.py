from dataclasses import dataclass

@dataclass
class SearchOptions:
    path: str
    pattern: str
    use_regex: bool = False
    case_sensitive: bool = False
    directory_mode: bool = False
    