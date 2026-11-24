"""UAES U19: evidence for the revision 4 customer engineering approval."""

from datetime import date

from audit.checker_lib.uaes.v1.engineering_approval import ApprovalR3


class ApprovalR4(ApprovalR3):
    def cover_signatures(self) -> dict[str, tuple[str, date | None]] | None:
        signed = super().cover_signatures()
        # A blank in the extracted text need not be a blank on the cover (e.g. vector ink).
        # Use the existing visual sign-off read before reporting a missing name or date.
        if signed and all(name and day is not None for name, day in signed.values()):
            return signed
        return None
