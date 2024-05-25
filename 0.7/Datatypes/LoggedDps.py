from collections import defaultdict

class LoggedDps:
    def __init__(self):
        self.dps = defaultdict(dict)
        