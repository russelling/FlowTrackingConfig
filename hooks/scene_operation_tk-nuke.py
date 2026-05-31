# Copyright (c) Studio. All Rights Reserved.
"""
scene_operation hook for tk-multi-workfiles2 / tk-nuke (episodic context).

Auto-versioning: on 'new' and 'version_up', scans the work area for existing
v### files and saves to the next available version.
File naming: 301_010_010_comp_v003.nk

Colour pipeline (first open only):
  Read (plates, raw)
    -> OCIOColorSpace  Camera log -> ACEScg   (update OCIO_CAMERA_INPUT)
       -> OCIOColorSpace  ACEScg -> AWG/Output
          -> OCIOFileTransform  per-shot CDL  (plates/301_010_010.cc)
             -> OCIOFileTransform  show LUT   (colour/luts/show.cube)
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

OCIO_CAMERA_INPUT = "ACES - ACEScg"    # update to your camera log space
OCIO_ACES_WORKING = "ACES - ACEScg"
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
                self._build_colour_template(context)
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

    def _build_colour_template(self, context):
        tk     = self.parent.sgtk
        fields = self._fields(context)

        plate_path = self._safe_resolve(tk, "ep_shot_plates",   fields, "PLACEHOLDER/plates/plate.####.exr")
        cdl_path   = self._safe_resolve(tk, "ep_shot_cdl",      fields, "PLACEHOLDER/plates/shot.cc")
        lut_path   = self._safe_resolve(tk, "ep_shot_show_lut", fields, "PLACEHOLDER/colour/luts/show.cube")

        plate_ok = os.path.exists(os.path.dirname(plate_path))
        cdl_ok   = os.path.exists(cdl_path)
        lut_ok   = os.path.exists(lut_path)

        def p(s): return s.replace("\\", "/")

        x, y = 100, 100

        read = nuke.createNode("Read", inpanel=False)
        read["file"].setValue(p(plate_path))
        read["raw"].setValue(True)
        read["colorspace"].setValue("raw")
        read["label"].setValue("PLATES\n[value file]")
        read.setXYpos(x, y); y += 120

        cs_in = nuke.createNode("OCIOColorSpace", inpanel=False)
        cs_in.setInput(0, read)
        cs_in["in_colorspace"].setValue(OCIO_CAMERA_INPUT)
        cs_in["out_colorspace"].setValue(OCIO_ACES_WORKING)
        cs_in["label"].setValue("Camera Log -> ACEScg\nSet in_colorspace per camera")
        cs_in.setXYpos(x, y); y += 120

        cs_awg = nuke.createNode("OCIOColorSpace", inpanel=False)
        cs_awg.setInput(0, cs_in)
        cs_awg["in_colorspace"].setValue(OCIO_ACES_WORKING)
        cs_awg["out_colorspace"].setValue(OCIO_AWG_OUTPUT)
        cs_awg["label"].setValue("ACEScg -> AWG / Output")
        cs_awg.setXYpos(x, y); y += 120

        cdl = nuke.createNode("OCIOFileTransform", inpanel=False)
        cdl.setInput(0, cs_awg)
        cdl["file"].setValue(p(cdl_path))
        cdl["direction"].setValue("forward")
        cdl["interpolation"].setValue("linear")
        cdl["label"].setValue("Shot CDL\n[value file]")
        cdl.setXYpos(x, y)
        if not cdl_ok:
            nuke.warning("[colour] CDL not found: %s" % cdl_path)
        y += 120

        lut = nuke.createNode("OCIOFileTransform", inpanel=False)
        lut.setInput(0, cdl)
        lut["file"].setValue(p(lut_path))
        lut["direction"].setValue("forward")
        lut["interpolation"].setValue("tetrahedral")
        lut["label"].setValue("Show LUT\n[value file]")
        lut.setXYpos(x, y)
        if not lut_ok:
            nuke.warning("[colour] Show LUT not found: %s" % lut_path)
        y += 120

        viewer = nuke.createNode("Viewer", inpanel=False)
        viewer.setInput(0, lut)
        viewer["viewerProcess"].setValue("None")
        viewer.setXYpos(x, y)

        nuke.message(
            "Colour pipeline loaded.\n\n"
            "Plates : %s\n  %s\n\nCDL : %s\n  %s\n\nLUT : %s\n  %s\n\n"
            "Set Camera Log -> ACEScg in_colorspace to match your camera."
            % ("OK" if plate_ok else "NOT FOUND", plate_path,
               "OK" if cdl_ok   else "NOT FOUND", cdl_path,
               "OK" if lut_ok   else "NOT FOUND", lut_path)
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
