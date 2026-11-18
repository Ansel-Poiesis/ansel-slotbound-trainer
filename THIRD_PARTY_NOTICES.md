# Third-party notices

The trainer's source code, vector icons and generated PNG/ICO assets are
copyright 2026 Ansel and distributed under the [MIT License](LICENSE).

The Windows executable bundles CPython and Tcl/Tk. Their original notices are
included in `THIRD_PARTY_LICENSES/` in this repository and in release ZIPs.
The CPython third-party license appendix is included as well, together with
the OpenSSL 3.5.7 Apache-2.0 license and libffi's MIT-style notice for native
libraries shipped with this CPython runtime.
PyInstaller 6.22.3 is a build tool under GPL-2.0-or-later with its bootloader
and packaging exception. See [its official license](https://pyinstaller.org/en/stable/license.html)
and the included original license text. The exception permits distribution
of applications under their own compatible license.

The application itself requires only the Python standard library. `pefile`
and the other PyInstaller dependencies in `requirements-build.txt` are build
dependencies, excluded from the application. Pillow is
used exclusively to regenerate project-owned icons during development;
its original HPND license is included. It is not bundled in the executable.

No LJD, cryptography, pefile, proprietary game executable, game DLL, game
archive, extracted art, decompiled game code, bytecode or player save is
distributed. The save editor loads `lua51.dll` from the user's selected game
installation only when requested. Its rights and those of the game remain
with their respective owners; this project's MIT license grants no rights
over them.

Slotbound / 转运军团 is developed and published by Optima Arch. Steam AppID:
[4459590](https://store.steampowered.com/app/4459590/Slotbound/).
This is an independent project by Ansel. FLiNG, 风灵月影 and 3DM are referenced
only to describe potential distribution channels; no official affiliation,
endorsement or collection acceptance is claimed.
