# Third-party software

Apache-2.0 covers TupoNVR's own code. Dependencies and container components retain their own licenses; the image's OCI license label describes the application, not every included package.

- The vendored MediaMTX WebRTC reader retains its MIT text at `frontend/public/MEDIAMTX-LICENSE.txt`, also shipped as `/MEDIAMTX-LICENSE.txt` by the web server.
- The frontend build extracts the installed runtime npm packages' license texts into `THIRD_PARTY_LICENSES.txt` and ships it with the static assets. This includes React, React DOM, the grid/video layout dependencies, and their runtime transitives. The build fails if a notice is missing; review that case before releasing instead of suppressing it. The generated file is ignored locally and regenerated from the lockfile/install.
- Python distributions retain their license metadata in site-packages. The source requirements and generated image SBOM identify the installed versions.
- MediaMTX is distributed as a separate upstream image. Preserve its notices and follow its upstream license.
- Debian FFmpeg and its linked codec/system libraries have their own terms, including GPL-enabled components in the Debian build. Package copyright files remain in the image. Before redistributing the image, review the exact binary packages and corresponding-source obligations; the project's Apache license does not replace those obligations. Debian source packages for the matching package versions are available through Debian's package archives. Source-code availability and any required notices must be handled by the image publisher.

Build base images, operating-system packages, and transitive Python dependency resolution can change between builds even with the application's direct dependency pins. Release SBOM/provenance and immutable published image digests record the concrete result; this is a repeatable build pipeline, not a promise of bit-for-bit identical future rebuilds. Use fixed release tags or digests for deployments, and review Dependabot updates and upstream security advisories.
