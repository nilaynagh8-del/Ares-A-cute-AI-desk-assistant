# Ares Build Journal

## June 20: Parts import + measuring

Imported all the parts into OnShape and measured the dimensions — the Seeed XIAO ESP32-S3 Sense, the round GC9A01 display, and the MakerHawk 1000mAh 1S LiPo all needed real measurements before modeling anything around them, since eyeballing dimensions on a face-shaped enclosure is a fast way to end up with a battery that doesn't fit.

**Time spent this session: 5 min**

---

## June 21: Modeling the back head

Modeled the back head shell. Splitting the head into front/back shells made sense since the round display needs to seat into a face-facing cutout, while the back needs to be hollowed out enough to route the mic/display wiring back toward the body.

![Back head shell in OnShape](images/back_head_cad.jpeg)

**Time spent this session: 1 hour**

---

## June 22: Modeling the front head

Modeled the front head shell — the half the round display actually seats into.

![Front head ring in OnShape](images/front_head_cad.jpeg)

**Time spent this session: 1 hour**

---

## June 23: Modeling the body

Modeled the body. This one needed to account for the battery bay, a wire pass-through up to the head, and mounting points for the XIAO board.

![Body shell in OnShape](images/body_cad.jpeg)

**Time spent this session: 2 hours**

---

## June 24: First print + fit test

Printed all three parts to test fit, and — as expected — found a few things that only show up once you're holding the physical part rather than looking at it on screen.

**No spot for the screen's on/off switch.** Had to go back into the model and add a cutout for it.

**Time spent this session: 30 min**

---

## June 25: Fixing the head-to-body slot

The head-to-body slot wasn't sized right, so the head didn't seat into the body cleanly. Reworked the cutout geometry so it actually slots in.

**Time spent this session: 30 min**

---

## June 26: Reshaping the battery slot

The battery slot needed reshaping, plus a new hole to route the battery wire from the body up into the head.

**Time spent this session: 30 min**

---

## June 27: Reprint + working fit

Reprinted the parts with all the fixes from the last few sessions, and this time everything slotted together correctly.

**Time spent this session: 10 min**

---

## June 28: Extending the battery wires

The battery cable turned out to be a little too short once everything was in its final position inside the enclosure, so I extended the wires rather than redesigning the bay around a fixed cable length.

![Battery bay with extended wires](images/battery_bay.jpeg)

**Time spent this session: 30 min**

---

## June 29: Soldering XIAO headers

Soldered headers onto the XIAO ESP32-S3 so it could actually seat onto the board mounts in the enclosure.

**Time spent this session: 1 hour**

---

## June 30: Full assembly

Assembled everything — head, body, display, battery, XIAO — into one working unit for the first time.

![Head open showing XIAO ESP32-S3 and display electronics](images/electronics_open.jpeg)

**Time spent this session: 30 min**

---

## July 1: Firmware/software troubleshooting

Spent this session troubleshooting the code to get everything talking to each other correctly — display driving, mic streaming, and getting the board to boot into a working state. Used AI assistance for parts of this rather than debugging blind.

![Ares fully assembled with eyes animated on the round display](images/final_assembly.jpeg)

**Time spent this session: 1 hour 30 min**

---

*Ares, up and running.*
