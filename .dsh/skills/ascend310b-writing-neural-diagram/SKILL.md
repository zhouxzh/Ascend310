---
name: ascend310b-writing-neural-diagram
description: Audit TikZ neural-network and encoder-decoder diagrams for connector paths crossing module or text boxes. 用于绘制、审查或修复神经网络结构图、编码器—解码器框图、残差连线和跨列注意力连线；检查几何冲突与 sidecar 漂移。
compatibility: 需要 Python 3 和本套件的 ascend310b_writing_neural_diagram_audit Host/MCP 工具；另行使用 XeLaTeX/PDF 做最终视觉检查。
---

# Neural diagram path audit

Use `ascend310b_writing_neural_diagram_audit` after changing a TikZ neural-network diagram. The audit is geometry-first: it does not infer intent from appearance and it does not certify a figure merely because LaTeX compiles.

## Required layout contract

Keep a sidecar JSON beside the TikZ source, normally `<source>.layout.json`:

```json
{
  "source_sha256": "<sha256 of the TikZ source>",
  "boxes": [
    {"name": "encoder_attention", "kind": "module", "rect": [0.5, 2.0, 2.5, 2.5]}
  ],
  "paths": [
    {
      "name": "encoder_residual_1",
      "polyline": [[0.5, 2.0], [0.2, 2.0], [0.2, 2.5], [0.5, 2.5]],
      "allowed_boxes": []
    }
  ]
}
```

Coordinates must use the same TikZ canvas as the source. `rect` is `[xmin, ymin, xmax, ymax]`. A path is a polyline of points. A path may enter only boxes explicitly listed in `allowed_boxes`; touching a box boundary is reported as a collision. A deliberate T-junction may declare the other path name in `allow_crossing_with`; this suppresses only that declared path-pair warning and does not suppress box collisions.

`source_sha256` binds the sidecar to the exact TikZ source. A mismatch is an error. Missing hash is a warning by default and an error when release mode uses `requireHash=true`.

## Review sequence

1. Name every visible module/text rectangle in the sidecar.
2. Represent every arrow and residual route as a polyline, including intermediate corners.
3. Update `source_sha256` after every TikZ source movement or connector change.
4. Run the tool with the source and sidecar. Use `requireHash=true` for release checks.
5. Fix every `error` before visual review. Treat path crossings as warnings unless the crossing is semantically intentional and declared with `allow_crossing_with` in the sidecar.
6. Check compiled boundary-to-boundary arrow gaps within each column: ordinary gaps must be equal; branch gaps must be equal within their class and longer than ordinary gaps (see the spacing rules below).
7. Compile the PDF and inspect the final-size render. Geometry audit catches source-coordinate collisions; raster review catches labels, arrowheads, and glyphs that visually touch after scaling.

## Routing rules for neural diagrams

- Main flow uses the centerline between consecutive modules.
- Residual routes use a dedicated outer channel on the encoder or decoder side; never run a vertical residual segment through another module.
- Encoder-to-decoder `K,V` uses the empty gap between columns, with its label outside all boxes.
- Factor-modulation paths use a separate dashed outer channel or a dashed projector box; do not draw long dashed lines through explanatory text.
- Input/output arrows attach to node anchors (`.north`, `.south`, `.east`, `.west`) rather than guessed coordinates.

## Uniform connector spacing within each column

- Use two shared boundary-to-boundary gaps per column: `normal_gap` for ordinary consecutive modules and `branch_gap` for connections that introduce a residual or other branch. Require `branch_gap > normal_gap`; use a visibly larger gap, for example 1.5--2 times `normal_gap`, as a starting point rather than a mandatory ratio.
- Ordinary straight arrows in the same column must have equal visible lengths. Measure the free gap from the preceding rendered box boundary to the following box boundary, not the distance between box centers. Variable-height labels must not change the free gaps.
- Give all branching connections in the same column the same longer free gap. Place the branch point inside that free gap, preferably at its midpoint, with clearance from both module boundaries and the arrowhead. Do not branch inside a module or on its text.
- A residual must tap the input before the sublayer it bypasses and merge at the corresponding Add & Norm. Increasing the gap or moving the route must not change this connectivity.
- In TikZ, place each next module relative to the preceding module's boundary, then use `.south` and `.north` anchors for downward flow (reverse for upward flow). Define a named branch coordinate between those anchors; route residuals through dedicated outer channels. Do not use fixed center-to-center row spacing as a substitute for equal edge-to-edge gaps.
- Use the same arrowhead size, line width, and endpoint shortening for comparable main-flow arrows. If branches are drawn as separate segments, keep the sum of their collinear visible segments equal to `branch_gap`.
- Check the gap classes using compiled node bounds. Compare ordinary gaps with other ordinary gaps and branching gaps with other branching gaps; do not flag the deliberately longer branch gaps as inconsistent. Check both columns, and align corresponding rows when possible without violating their gap classes.
- Re-run the collision audit after spacing changes and inspect the final-size PDF. The current collision tool does not automatically certify equal arrow lengths; record this as a separate spacing check, not as an implied result of `PASS: 0 finding(s)`.

## Limits

The audit cannot recover exact node dimensions from arbitrary TikZ styles. The sidecar is an explicit geometry contract and must be updated when boxes move. Hashing proves the sidecar corresponds to the current source, but not that its manually entered rectangles match rendered node bounds. A passing audit is necessary but not sufficient: compile and inspect the final PDF at the manuscript's actual size.
