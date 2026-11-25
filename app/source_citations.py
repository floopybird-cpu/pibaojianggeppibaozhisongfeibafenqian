"""One reference per original, with local page locators and full anchor audit."""
from collections import OrderedDict


class SourceValidationError(ValueError):
    """One authoring response for all currently visible source repair locations."""
    def __init__(self, issues):
        self.issues=issues
        super().__init__('\n'.join(f"{issue['message']}（{', '.join(issue['paths'])}）" for issue in issues))


class Citations:
    def __init__(self, identities, snapshot):
        self.anchors = {aid: snapshot.anchor(aid) for aid in identities}
        self.groups = OrderedDict()
        self.numbers = {}
        for aid, anchor in self.anchors.items():
            key = anchor['source_sha']
            self.groups.setdefault(key, []).append(aid)
            self.numbers[aid] = list(self.groups).index(key) + 1

    def labels(self, identities):
        groups = OrderedDict()
        for aid in identities:
            groups.setdefault(self.numbers[aid], set()).add(self.anchors[aid]['page'])
        return [(number, '[' + str(number) + '，第' + '、'.join(map(str, sorted(pages))) + '页]')
                for number, pages in groups.items()]
