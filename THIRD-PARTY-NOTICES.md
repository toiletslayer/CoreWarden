# Third-party notices

CoreWarden's direct runtime dependencies are distributed under the following
licenses:

| Dependency | Role | License |
|---|---|---|
| OpenAI Python SDK | OpenAI Responses provider | Apache-2.0 |
| Pydantic | validated configuration and diagnosis models | MIT |
| Strands Agents | Bedrock agent and tool runtime | Apache-2.0 |
| boto3 / botocore | AWS credential and Bedrock runtime integration through Strands | Apache-2.0 |
| AWS Common Runtime for Python (awscrt) | AWS `login_session` credential support | Apache-2.0 |

The Windows bundle is created with PyInstaller, which is GPL-2.0-or-later with
the PyInstaller bootloader exception permitting distribution of the resulting
application.

The bundle also contains transitive Python and Tcl/Tk dependencies. Their package
metadata and license files are retained in the PyInstaller onedir bundle where
provided. These projects remain the property of their respective copyright
holders; inclusion does not imply endorsement.

This notice is informational and does not replace the license terms shipped with
CoreWarden or its dependencies.
