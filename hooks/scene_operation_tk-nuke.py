# Copyright (c) Studio. All Rights Reserved.
"""
scene_operation hook for tk-multi-workfiles2 / tk-nuke (episodic context).

Auto-versioning: on 'new' and 'version_up', scans the work area for existing
v### files and saves to the next available version.
File naming: 301_010_010_comp_v003.nk

color pipeline (first open only):
  Read (plates, raw)
    -> OCIOColorSpace  Camera log -> ACEScg   (update OCIO_CAMERA_INPUT)
       -> OCIOColorSpace  ACEScg -> AWG/Output
          -> OCIOFileTransform  per-shot CDL  (plates/301_010_010.cc)
             -> OCIOFileTransform  show LUT   (color/luts/show.cube)
                -> Viewer  viewerProcess=None

OCIO config: ACES 1.3
Update OCIO_CAMERA_INPUT to match your camera log:
  ARRI LogC3  : "Input - ARRI - Curve - LogC3 - EI800"
  RED Log3G10 : "Input - RED - Curve - Log3G10"
  Sony SLog3  : "Input - Sony - Curve - SLog3 - SGamut3"
"""

import os
import re
import glob

import nuke
import sgtk

HookClass = sgtk.get_hook_baseclass()

OCIO_CAMERA_INPUT = "Input - ARRI - Curve - LogC4 - EI800"
OCIO_ACES_WORKING = "ACES - ACEScg"
OCIO_LOGC4_OUTPUT = "Input - ARRI - Curve - LogC4 - EI800"
OCIO_AWG_OUTPUT   = "Output - Rec.709"


class SceneOperation(HookClass):

    def execute(self, operation, file_path, context, parent_action,
                file_version, read_only, **kwargs):

        engine = self.parent.engine

        if operation == "current_path":
            root_name = nuke.root().name()
            return root_name if root_name != "Root" else ""

        elif operation == "open":
            nuke.scriptOpen(file_path)
            if read_only:
                nuke.root()["lock_range"].setValue(True)

        elif operation == "save":
            nuke.scriptSave()

        elif operation == "save_as":
            old = nuke.root().name()
            nuke.scriptSaveAs(file_path, overwrite=1)
            if old != file_path:
                engine.save_context_to_script()

        elif operation == "reset":
            nuke.scriptClear()
            return True

        elif operation == "new":
            nuke.scriptClear()
            resolved = self._resolve_new_path(context)
            self._ensure_dir(resolved)
            nuke.scriptSaveAs(resolved, overwrite=0)
            engine.save_context_to_script()
            if self._is_first_version(context):
                self._build_color_template(context)
                nuke.scriptSave()
            return resolved

        elif operation == "version_up":
            resolved = self._resolve_next_version(context)
            self._ensure_dir(resolved)
            nuke.scriptSaveAs(resolved, overwrite=0)
            engine.save_context_to_script()
            return resolved

    def _work_template(self, context):
        return self.parent.sgtk.templates["ep_nuke_shot_work"]

    def _fields(self, context):
        fields = context.as_template_fields(self._work_template(context))
        fields.setdefault("nuke_extension", "nk")
        return fields

    def _resolve_new_path(self, context):
        return self._resolve_next_version(context, start_at=1)

    def _resolve_next_version(self, context, start_at=None):
        template = self._work_template(context)
        fields   = self._fields(context)
        existing = self._existing_versions(template, fields)
        if start_at is not None:
            next_v = max(start_at, (max(existing) + 1) if existing else start_at)
        else:
            next_v = (max(existing) + 1) if existing else 1
        fields["version"] = next_v
        return template.apply_fields(fields)

    def _existing_versions(self, template, fields):
        search = dict(fields)
        search["version"] = 0
        try:
            glob_path = template.apply_fields(search)
        except Exception:
            return []
        glob_path = re.sub(r"v\d{3,}", "v*", glob_path)
        versions = []
        for path in glob.glob(glob_path):
            m = re.search(r"v(\d{3,})", os.path.basename(path))
            if m:
                versions.append(int(m.group(1)))
        return sorted(versions)

    def _is_first_version(self, context):
        return len(self._existing_versions(
            self._work_template(context), self._fields(context))) <= 1

    @staticmethod
    def _ensure_dir(path):
        folder = os.path.dirname(path)
        if folder and not os.path.exists(folder):
            os.makedirs(folder)

    def _build_color_template(self, context):
        tk     = self.parent.sgtk
        fields = self._fields(context)

        plate_path = self._safe_resolve(tk, "ep_shot_plates",   fields, "PLACEHOLDER/plates/plate.####.exr")
        cdl_path   = self._safe_resolve(tk, "ep_shot_cdl",      fields, "PLACEHOLDER/plates/shot.cdl")
        lut_path   = self._safe_resolve(tk, "ep_shot_show_lut", fields, "color/luts/ARRILogC4_SEV_S3_V3_digital_R709.cube")

        plate_ok = os.path.exists(os.path.dirname(plate_path))
        cdl_ok   = os.path.exists(cdl_path)
        lut_ok   = os.path.exists(lut_path)

        def p(s): return s.replace("\\", "/")

        x, y = 200, 100

        # ── Camera plates Read (LogC4 - convert to ACEScg) ────────────────
        read_plates = nuke.createNode("Read", inpanel=False)
        read_plates["file"].setValue(p(plate_path))
        read_plates["raw"].setValue(True)
        read_plates["colorspace"].setValue("raw")
        read_plates["label"].setValue("CAMERA PLATES (LogC4)\n[value file]")
        read_plates.setXYpos(x - 150, y)

        cs_logc4 = nuke.createNode("OCIOColorSpace", inpanel=False)
        cs_logc4.setInput(0, read_plates)
        cs_logc4["in_colorspace"].setValue(OCIO_CAMERA_INPUT)
        cs_logc4["out_colorspace"].setValue(OCIO_ACES_WORKING)
        cs_logc4["label"].setValue("LogC4 → ACEScg")
        cs_logc4.setXYpos(x - 150, y + 120)

        # ── VFX pull Read (already ACEScg - no conversion) ────────────────
        read_vfx = nuke.createNode("Read", inpanel=False)
        read_vfx["file"].setValue("REPLACE_WITH_VFX_PATH.####.exr")
        read_vfx["raw"].setValue(True)
        read_vfx["colorspace"].setValue("raw")
        read_vfx["label"].setValue("VFX PULL (ACEScg)\n[value file]")
        read_vfx.setXYpos(x + 150, y)

        # ── Merge plates + VFX in ACEScg ──────────────────────────────────
        y += 240
        merge = nuke.createNode("Merge2", inpanel=False)
        merge.setInput(0, cs_logc4)
        merge.setInput(1, read_vfx)
        merge["label"].setValue("Working: ACEScg")
        merge.setXYpos(x, y)

        # ── Output: ACEScg → LogC4 ────────────────────────────────────────
        y += 120
        cs_out = nuke.createNode("OCIOColorSpace", inpanel=False)
        cs_out.setInput(0, merge)
        cs_out["in_colorspace"].setValue(OCIO_ACES_WORKING)
        cs_out["out_colorspace"].setValue(OCIO_LOGC4_OUTPUT)
        cs_out["label"].setValue("ACEScg → LogC4 (output)")
        cs_out.setXYpos(x, y)

        # ── CDL (per-shot grade in LogC4) ─────────────────────────────────
        y += 120
        cdl = nuke.createNode("OCIOFileTransform", inpanel=False)
        cdl.setInput(0, cs_out)
        cdl["file"].setValue(p(cdl_path))
        cdl["direction"].setValue("forward")
        cdl["interpolation"].setValue("linear")
        cdl["label"].setValue("Shot CDL\n[value file]")
        cdl.setXYpos(x, y)
        if not cdl_ok:
            nuke.warning("[color] CDL not found: %s" % cdl_path)

        # ── Show LUT (LogC4 → Rec.709) ────────────────────────────────────
        y += 120
        lut = nuke.createNode("OCIOFileTransform", inpanel=False)
        lut.setInput(0, cdl)
        lut["file"].setValue(p(lut_path))
        lut["direction"].setValue("forward")
        lut["interpolation"].setValue("tetrahedral")
        lut["label"].setValue("Show LUT → Rec.709\n[value file]")
        lut.setXYpos(x, y)
        if not lut_ok:
            nuke.warning("[color] Show LUT not found: %s" % lut_path)

        # ── Viewer (display in Rec.709, working space ACEScg) ─────────────
        y += 120
        viewer = nuke.createNode("Viewer", inpanel=False)
        viewer.setInput(0, lut)
        viewer["viewerProcess"].setValue("None")
        viewer["label"].setValue("Rec.709 output")
        viewer.setXYpos(x, y)

        nuke.message(
            "Color pipeline loaded.\n\n"
            "Working space: ACEScg\n\n"
            "Camera Plates : LogC4 → ACEScg on read\n  %s\n\n"
            "VFX Pulls : Replace path in VFX PULL Read\n  (raw read, already ACEScg)\n\n"
            "Output chain: ACEScg → LogC4 → CDL → Show LUT → Rec.709\n\n"
            "CDL : %s\n  %s\n\n"
            "LUT : %s\n  %s"
            % (plate_path,
               "OK" if cdl_ok else "NOT FOUND", cdl_path,
               "OK" if lut_ok else "NOT FOUND", lut_path)
        )

    @staticmethod
    def _safe_resolve(tk, template_name, fields, fallback):
        try:
            tmpl   = tk.templates[template_name]
            needed = {k: fields[k] for k in tmpl.keys if k in fields}
            return tmpl.apply_fields(needed)
        except Exception as exc:
            nuke.warning("[colour] Could not resolve '%s': %s" % (template_name, exc))
            return fallback
