# Law No. 2004-63 — Personal data compliance checklist

Tunisian organic law on the protection of personal data. Applies to: CIN scans, Carte Grise scans, phone numbers, profile data, ratings, chat content, and location history.

**Gate:** none of the production storage of real identity documents may be enabled before every item below is checked.

- [ ] Determine controller/processor roles for the platform and any hosting provider.
- [ ] Declare processing activities to ANPDP (Autorité Nationale de Protection des Données Personnelles) where required.
- [ ] Lawful basis documented for each data category (contract execution for ride booking; explicit consent for identity verification).
- [ ] Minimization: store only the object key + review status for documents; no duplicate copies; no thumbnails.
- [x] Access control: private S3 bucket, short-lived signed URLs (default 5 min), owner-or-staff-only download endpoint. — implemented in `apps.verification`.
- [ ] Retention policy for rejected documents and inactive accounts (define and implement deletion).
- [x] IP addresses hashed (SHA-256) in audit logs — implemented in `apps.audit.middleware`.
- [ ] Right of access / rectification / deletion endpoints for users (define endpoints and SLA).
- [ ] Encryption at rest for the documents bucket; encryption in transit (TLS) everywhere.
- [ ] Data subject breach-notification procedure (72h target).
- [ ] Review date set; legal counsel sign-off recorded here.

## Enforcement status

- The checklist is **not signed off**. Real identity-document uploads are therefore
  disabled at two levels:
  1. `IDENTITY_DOCUMENT_UPLOADS_ENABLED` defaults to `False` in
     `config/settings/prod.py` (and to `DEBUG` elsewhere);
  2. `POST /api/v1/verification/documents/upload-url/` returns **403** when the
     flag is off — the gate is enforced in the API, not just documented.
- To enable real uploads in production: every unchecked item above is completed,
  counsel signs off below, and then `IDENTITY_DOCUMENT_UPLOADS_ENABLED=true` is
  set in the production environment.

## Sign-off

| Field | Value |
| --- | --- |
| Reviewed by | _pending legal counsel_ |
| Review date | _pending_ |
| Signature / reference | _pending_ |
| Upload gate unlocked on | _not unlocked_ |
