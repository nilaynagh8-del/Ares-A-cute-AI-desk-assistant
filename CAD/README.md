# CAD

3D model files for the Ares enclosure.

## Files

| File | Purpose |
|------|---------|
| `Ares-robot-assembled.step` | Full assembled model for reference/editing |
| `Body.stl` | Printable body/base |
| `Head-Front.stl` | Printable front half of the head |
| `Head-Back.stl` | Printable back half of the head |

The enclosure is built around the Seeed XIAO ESP32-S3 Sense, Seeed Round Display
for XIAO, and a MakerHawk 1000mAh 1S LiPo battery.

## Camera cutout

The XIAO ESP32-S3 Sense can support a camera, but this build does not integrate
the included camera because its stock ribbon cable is too short for the enclosure.
The top cutout leaves a path for a future camera build: use a compatible camera or
longer ribbon cable, route it through the cutout, and update the firmware/desktop
software to forward frames for Gemini visual analysis.
