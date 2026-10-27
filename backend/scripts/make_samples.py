"""Generate the synthetic demo PDFs in data/samples (fictional institutions; no real regulatory text)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.services.synthetic import make_pdf  # noqa: E402

OUT = Path(__file__).resolve().parents[2] / "data" / "samples"
OUT.mkdir(parents=True, exist_ok=True)

H = lambda t: ("h1", t)  # noqa: E731
P = lambda t: ("p", t)  # noqa: E731

KYC_V1 = [H("Circular KYC/2023/01 - Customer Identification Policy"),
          H("1. Applicability"), P("1.1 This circular applies to all regulated entities onboarding individual customers after 1 April 2023."),
          H("2. Identity Verification"),
          P("2.1 A regulated entity shall verify the identity of every customer using an officially valid document before opening an account."),
          P("2.2 Verification must be completed within 30 days of account opening, failing which the account shall be restricted."),
          P("2.3 Video based customer identification is permitted only for resident individuals."),
          P("2.4 The minimum threshold for enhanced due diligence on cash deposits is Rs. 10,00,000 in a financial year."),
          H("3. Record Keeping"), P("3.1 Identification records shall be preserved for five years after the end of the business relationship."),
          P("3.2 Records of transactions above the threshold shall be kept in a searchable electronic form."),
          H("4. Penalties"), P("4.1 Non-compliance shall attract a penalty of 2% of the outstanding amount per month."),
          P("4.2 Repeated violations may be reported to the supervisory authority for further action.")]
KYC_V2 = [H("Circular KYC/2024/07 - Customer Identification Policy"),
          H("1. Applicability"), P("1.1 This circular applies to all regulated entities onboarding individual customers after 1 April 2024."),
          H("2. Identity Verification"),
          P("2.1 A regulated entity shall verify the identity of every customer using an officially valid document before opening an account."),
          P("2.2 Verification must be completed within 15 days of account opening, failing which the account shall be restricted."),
          P("2.3 Biometric authentication is additionally required for accounts expected to exceed the high value limit."),
          P("2.4 The minimum threshold for enhanced due diligence on cash deposits is Rs. 5,00,000 in a financial year."),
          H("3. Record Keeping"), P("3.1 Identification records shall be preserved for ten years after the end of the business relationship."),
          H("4. Penalties"), P("4.1 Non-compliance shall attract a penalty of 4% of the outstanding amount per month."),
          P("4.2 Repeated violations may be reported to the supervisory authority for further action."),
          H("5. Customer Grievances"), P("5.1 Every regulated entity must acknowledge a KYC related complaint within 3 working days.")]
# second, independent pair (another institution) that also drops the video-identification clause
PAIR_B1 = [H("Northwind Credit Union - Onboarding Standard v3"), H("1. Identification"),
           P("1.1 Staff shall record the identity of each new member using a government issued photo document."),
           P("1.2 Video based customer identification is permitted only for resident individuals."),
           P("1.3 Members must be informed of the privacy notice at the time of enrolment.")]
PAIR_B2 = [H("Northwind Credit Union - Onboarding Standard v4"), H("1. Identification"),
           P("1.1 Staff shall record the identity of each new member using a government issued photo document."),
           P("1.2 Members must be informed of the privacy notice at the time of enrolment."),
           P("1.3 Members shall receive a written confirmation of enrolment within 7 days.")]
for name, blocks in {"kyc_circular_v1.pdf": KYC_V1, "kyc_circular_v2.pdf": KYC_V2, "northwind_standard_v3.pdf": PAIR_B1, "northwind_standard_v4.pdf": PAIR_B2}.items():
    make_pdf(OUT / name, blocks)
    print("wrote", OUT / name)
