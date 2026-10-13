"""Task-scoped first catalog browse; historical advice never becomes evidence."""


class ExperienceBrowseGate:
    def __init__(self, enabled: bool):
        self.state = 'pending' if enabled else 'bypassed'
        self.reason = '' if enabled else 'unavailable'
        self.refusals = 0
        self.delivery = None

    def reject(self) -> bool:
        if self.state != 'pending':
            return False
        self.refusals += 1
        if self.refusals >= 2:
            self.bypass('noncompliant')
        return True

    def bypass(self, reason: str) -> None:
        if self.state == 'pending':
            self.state, self.reason = 'bypassed', reason
            self.delivery = None

    def satisfy(self) -> None:
        if self.state == 'pending':
            self.state, self.reason = 'satisfied', 'catalog_delivered'
            self.delivery = None

    def stage_delivery(self, call_id: str, payload: str) -> None:
        if self.state == 'pending' and call_id:
            self.delivery = (call_id, 'list_experiences', 'success', payload)

    def confirm_delivery(self, results) -> bool:
        if not self.has_delivery(results):
            return False
        self.satisfy()
        return True

    def has_delivery(self, results) -> bool:
        return self.state == 'pending' and self.delivery is not None and self.delivery in results
