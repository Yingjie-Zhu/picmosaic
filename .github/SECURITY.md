# Security Policy

## Supported versions

| Version | Supported          |
| ------- | ------------------ |
| 1.0.x   | :white_check_mark: |
| < 1.0   | :x:                |

## Reporting a vulnerability

Please **do not** report security vulnerabilities through public GitHub issues.

Instead, report them privately via GitHub Security Advisories:
[https://github.com/Yingjie-Zhu/picmosaic/security/advisories/new](https://github.com/Yingjie-Zhu/picmosaic/security/advisories/new)
("Report a vulnerability" on the repository's Security tab).

Include as much of the following as you can:

- A description of the issue and its impact.
- Steps to reproduce (a minimal proof of concept is ideal).
- Affected version(s) and environment (OS, Python version).

You can expect an acknowledgment within a few days. Please allow the maintainers a
reasonable window to investigate and release a fix before any public disclosure;
we will gladly credit you in the release notes unless you prefer otherwise.

## Scope notes

PicMosaic processes untrusted image files with Pillow and NumPy. Vulnerabilities in
those libraries should be reported upstream, but issues in how PicMosaic *uses* them
(e.g. path handling, cache files, resource exhaustion beyond the documented memory
guard) are in scope.
