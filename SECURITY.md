# Security policy

## Supported version

Before the first tagged release, only the current `main` branch is supported. Once releases begin, this section will list the supported versions.

## Reporting a vulnerability

Do not open a public issue for manuscript disclosure, ZIP/XML parser bypass, path traversal, generated-data leakage, credential exposure, or release-gate bypass. Use GitHub private vulnerability reporting when it is enabled. Otherwise, contact a maintainer through an established private channel. This file does not guess or publish an unconfirmed email address.

Include the affected version, a minimal synthetic reproduction, the impact, and any suggested mitigation. Do not attach an unpublished manuscript or third-party copyrighted content. When a private reporting channel is available, maintainers aim to acknowledge a report within seven days.

## Security boundary

The CLI performs no network requests and requires no API key. Import intentionally retains headings and metadata but not prose. EPUB processing rejects encryption/DRM, unsafe paths, external spine references, DTD/entity declarations, excessive member size, excessive total expansion, and suspicious compression ratios. Optional PDF processing uses local Poppler tools with no shell, rejects encrypted/password-protected or excessive input, and bounds tool time and captured output. Poppler remains an external parser; users should process only PDFs whose provenance and parser risk they accept.

SYS instructions in generated packs are not a security boundary. Information minimization and human non-reconstruction review are the controls against substitute-book generation.

The toolkit cannot guarantee an external AI provider's confidentiality, retention, training, or regional data handling. Users must review those terms before sending protected material.
