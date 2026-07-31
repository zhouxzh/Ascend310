# Third-Party Notices

## react-piano

- Repository: https://github.com/kevinsqi/react-piano
- Fixed commit: `a8fac9f1ab0aab8fd21658714f1ad9f14568feee`
- License: MIT
- Use in this project: the piano key proportions and keyboard-shortcut label
  behavior in `webui/src/components/Piano.tsx` were adapted. The local pointer,
  multi-touch, cancellation, focus-loss, and WebSocket behavior is original to
  this project.

Copyright (c) 2017 Kevin Siqi

Permission is hereby granted, free of charge, to any person obtaining a copy of
this software and associated documentation files (the "Software"), to deal in
the Software without restriction, including without limitation the rights to
use, copy, modify, merge, publish, distribute, sublicense, and/or sell copies of
the Software, and to permit persons to whom the Software is furnished to do so,
subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.

## Partitura voice separation

- Repository: https://github.com/CPJKU/partitura
- Fixed commit: `427ff875bd5a49a0eec894fdd7c6631ed7f597ea`
- License: Apache-2.0
- Use in this project: the Chew/Wu contig-mapping voice-separation implementation
  is vendored with local adaptations under `midi_ddsp_webui/vendor/partitura/`.
  The complete Apache-2.0 license text is retained at
  `midi_ddsp_webui/vendor/partitura/LICENSE`.

## Design References Without Copied Code

- ChordMiniApp: https://github.com/ptnghia-j/ChordMiniApp, fixed commit
  `33623b8885259f59c4005dad79b489aca8ae4ef9`, MIT. Referenced for the
  piano-visualizer hierarchy: compact context header, note timeline, dark
  canvas, hit line, keyboard alignment, legend, and adjacent transport. The
  local live-note history renderer and touch workbench are original; no
  ChordMiniApp source code or assets were copied.

- SpessaSynth: https://github.com/spessasus/SpessaSynth, fixed commit
  `0a9335304e8e88763d3ec74a850c5b8029586298`, Apache-2.0. Referenced for the
  separation of mobile keyboard, transport, and controller layers. No source
  code was copied.
- JSS-01: https://github.com/michaelkolesidis/javascript-software-synthesizer,
  AGPL-3.0. Referenced only for modular synthesizer visual organization. No
  source code was copied or adapted.
