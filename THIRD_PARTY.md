This project is a modified fork of:

* phi0x/omarchy-sennheiser-control, forked at commit 62c9ce77fa2c72f269d107af19e144d5e46ca768
  https://github.com/phi0x/omarchy-sennheiser-control — Sennheiser Control
  Source-Available License 1.0 (see LICENSE.md).
  The panel layout, shared controller, reconnect logic, build helper and SDP
  helper come from it. Modifications in this fork: the BTD 700 dongle,
  parametric EQ, firmware-check and plugin-update-check features were removed,
  and the headphone command set, equalizer and panel were rewritten for the
  ACCENTUM. Files derived from it remain under its license.

Protocol references used for the ACCENTUM port:

* xndbogdan/sennheiser-control
  https://github.com/xndbogdan/sennheiser-control — MIT.
  Its protocol notes supplied the ACCENTUM command map and feature set.
  backend/eq.py is a Python port of the "Gen1" equalizer maths and ACCENTUM
  constants in its crates/protocol/src/eq.rs, and tests/test_eq.py uses test
  vectors from its crates/protocol/tests/eq_gen1_vectors.json.
  backend/eq.py is distributed under MIT.
* hatemosphere/hdb630-control-macos, commit af0a23059f33f7b6ebe4de62e9af351116fc20ee
  https://github.com/hatemosphere/hdb630-control-macos — MIT.
  GAIA framing and command IDs informed the upstream Python port that
  backend/headphones.py is based on. backend/headphones.py, including the
  changes in this fork, is distributed under MIT.
  See licenses/hdb630-MIT.txt for the upstream notice.
* DanSmith888/omarchy-momentum4
  https://github.com/DanSmith888/omarchy-momentum4
  Its protocol notes were used to cross-check command IDs and to identify
  commands that must not be probed.

The widget imports the installed Omarchy shell's shared UI components at runtime.
This app is unofficial and is not affiliated with Sennheiser or Sonova.

Omarchy and its QML components, BlueZ/libbluetooth, Python, python-dbus, GCC and
installed fonts remain separately installed dependencies under their own
licenses. No vendor software, firmware image, font file or Sennheiser logo is
bundled. The C helper links to the system libbluetooth; its source is included
and no prebuilt binary is distributed in this repository.

The no-resale condition on the rest of this application does not restrict rights
granted by the MIT license for the components identified above.
