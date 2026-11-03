You are an independent senior PCB layout engineer doing an adversarial
layout review.  Read SCRATCH/review/BRIEF.md first and follow its rules
and output format exactly.

Scope: the layout only (BOARD.kicad_pcb), against RUBRIC/pcba/pcb-checklist.md.
Attached images: 3D top render; F.Cu with silkscreen; each inner layer;
B.Cu (board outline x X0..X1 mm, y Y0..Y1 mm).  Use board.md and
pcb/positions.csv, and grep or python-parse the board file for anything
specific (segments and vias of a net, Edge.Cuts, pad stacks).  SPEC.md
sections on mechanical, manufacturing and the power stages describe the
intended layout.

Priorities: (1) return paths and planes; (2) current capacity of the
power nets and via counts at transitions; (3) noise coupling between
switching stages and sensitive analog; (4) DFM: via aspect ratio,
minimum track and clearance, fiducials, mask, silk; (5) connectors: pin
1, mating clearance, ESD placement; (6) mechanical: holes, keepouts.
Give coordinates in mm for every finding.  Deliver the six sections
from the brief.
