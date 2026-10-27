from pathlib import Path

from app.services.synthetic import make_pdf

V1 = [("h1", "1. Scope"), ("p", "1.1 This policy applies to all customers opening accounts after the effective date."),
      ("h1", "2. Identity Verification"),
      ("p", "2.1 The institution shall verify the identity of each customer using an officially valid document."),
      ("p", "2.2 Verification must be completed within 30 days of account opening."),
      ("p", "2.3 Video based verification is permitted only for resident customers."),
      ("h1", "3. Penalties"), ("p", "3.1 A penalty of 2% of the outstanding amount applies for late compliance.")]
V2 = [("h1", "1. Scope"), ("p", "1.1 This policy applies to all customers opening accounts after the effective date."),
      ("h1", "2. Identity Verification"),
      ("p", "2.1 The institution shall verify the identity of each customer using an officially valid document."),
      ("p", "2.2 Verification must be completed within 15 days of account opening."),
      ("p", "2.4 Biometric authentication is additionally required for high value accounts."),
      ("h1", "3. Penalties"), ("p", "3.1 A penalty of 2% of the outstanding amount applies for late compliance.")]


def pdf(tmp_path: Path, name: str, blocks) -> Path:
    return make_pdf(tmp_path / name, blocks)
