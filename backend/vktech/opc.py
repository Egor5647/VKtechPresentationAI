"""OPC package operations. Source XML is preserved; mutable graphs are copied."""
from __future__ import annotations
import copy
import hashlib
import io
import posixpath
import zipfile
from pathlib import PurePosixPath
from lxml import etree as ET

NS = {"p": "http://schemas.openxmlformats.org/presentationml/2006/main",
      "a": "http://schemas.openxmlformats.org/drawingml/2006/main",
      "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
      "rel": "http://schemas.openxmlformats.org/package/2006/relationships",
      "ct": "http://schemas.openxmlformats.org/package/2006/content-types",
      "dgm": "http://schemas.openxmlformats.org/drawingml/2006/diagram"}
PARSER = ET.XMLParser(resolve_entities=False, no_network=True, remove_blank_text=False)


def xml(data: bytes):
    if b"<!DOCTYPE" in data.upper():
        raise ValueError("DTD declarations are not supported")
    return ET.fromstring(data, parser=PARSER)


def serialize(root):
    return ET.tostring(root, xml_declaration=True, encoding="UTF-8", standalone=True)


def relpath(part):
    parent, name = posixpath.split(part)
    return posixpath.join(parent, "_rels", name + ".rels")


def resolve(part, target):
    path = posixpath.normpath(posixpath.join(posixpath.dirname(part), target)) if not target.startswith("/") else target[1:]
    if path.startswith("../") or path == "..":
        raise ValueError("Relationship escapes package")
    return path


class Package:
    def __init__(self, data: bytes, max_bytes=512 * 1024**2, max_entries=10000):
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            info = z.infolist()
            if len(info) > max_entries or sum(x.file_size for x in info) > max_bytes:
                raise ValueError("PPTX archive exceeds allowed size")
            names = [x.filename for x in info]
            if len(names) != len(set(names)):
                raise ValueError("Duplicate archive entries")
            if any(PurePosixPath(n).is_absolute() or ".." in PurePosixPath(n).parts or "\\" in n for n in names):
                raise ValueError("Invalid archive path")
            self.parts = {x.filename: z.read(x) for x in info if not x.is_dir()}
        self.source_hash = hashlib.sha256(data).hexdigest()
        if "ppt/presentation.xml" not in self.parts:
            raise ValueError("Not a PowerPoint presentation")
        self.validate()

    def root(self, part):
        return xml(self.parts[part])

    def relations(self, part):
        rp = relpath(part)
        return list(self.root(rp)) if rp in self.parts else []

    def target(self, part, rid):
        for rel in self.relations(part):
            if rel.get("Id") == rid and rel.get("TargetMode") != "External":
                return resolve(part, rel.get("Target"))
        return None

    def related(self, part, kind):
        return next((resolve(part, r.get("Target")) for r in self.relations(part)
                     if r.get("Type", "").endswith("/" + kind) and r.get("TargetMode") != "External"), None)

    def slides(self):
        root = self.root("ppt/presentation.xml")
        return [self.target("ppt/presentation.xml", n.get("{" + NS["r"] + "}id"))
                for n in root.findall("p:sldIdLst/p:sldId", NS)]

    def validate(self):
        for name, data in self.parts.items():
            if name.endswith((".xml", ".rels")):
                root = xml(data)
                if name.endswith(".rels"):
                    part = name.replace("/_rels/", "/")[:-5] if name != "_rels/.rels" else ""
                    for rel in root:
                        if rel.get("TargetMode") != "External" and resolve(part, rel.get("Target", "")) not in self.parts:
                            raise ValueError(f"Broken relationship in {name}: {rel.get('Id')}")

    def clone_graph(self, source: "Package", part: str, prefix: str, mapping=None):
        mapping = {} if mapping is None else mapping
        if part in mapping:
            return mapping[part]
        # Existing resources from this template remain byte-identical.
        immutable = any(x in part for x in ("/slideLayouts/", "/slideMasters/", "/theme/", "/media/", "/fonts/"))
        if immutable and self.parts.get(part) == source.parts.get(part):
            mapping[part] = part
            return part
        parent, name = posixpath.split(part)
        new = posixpath.join(parent, prefix + "_" + name)
        mapping[part] = new
        self.parts[new] = source.parts[part]
        rels = source.relations(part)
        if rels:
            rr = ET.Element("{" + NS["rel"] + "}Relationships", nsmap={None: NS["rel"]})
            for r in rels:
                r = copy.deepcopy(r)
                if r.get("TargetMode") != "External":
                    target = source.target(part, r.get("Id"))
                    dest = self.clone_graph(source, target, prefix, mapping)
                    r.set("Target", posixpath.relpath(dest, posixpath.dirname(new)))
                rr.append(r)
            self.parts[relpath(new)] = serialize(rr)
        ct = self.root("[Content_Types].xml")
        srcct = source.root("[Content_Types].xml")
        for typ in srcct:
            if typ.get("PartName") == "/" + part:
                typ = copy.deepcopy(typ)
                typ.set("PartName", "/" + new)
                if not any(t.get("PartName") == "/" + new for t in ct):
                    ct.append(typ)
            elif typ.get("Extension") and not any(t.get("Extension") == typ.get("Extension") for t in ct):
                ct.append(copy.deepcopy(typ))
        self.parts["[Content_Types].xml"] = serialize(ct)
        return new

    def set_slides(self, parts):
        root = self.root("ppt/presentation.xml")
        lst = root.find("p:sldIdLst", NS)
        if lst is None:
            lst = ET.SubElement(root, "{" + NS["p"] + "}sldIdLst")
        lst.clear()
        # Old custom shows and sections refer to old slide IDs.
        for node in list(root):
            if ET.QName(node).localname in {"custShowLst", "extLst"}:
                root.remove(node)
        rr = self.root(relpath("ppt/presentation.xml"))
        for rel in list(rr):
            if rel.get("Type", "").endswith("/slide"):
                rr.remove(rel)
        used = {x.get("Id") for x in rr}
        for i, part in enumerate(parts):
            rid = f"rIdGenerated{i + 1}"
            assert rid not in used
            ET.SubElement(rr, "{" + NS["rel"] + "}Relationship", Id=rid, Type=NS["r"] + "/slide", Target=posixpath.relpath(part, "ppt"))
            ET.SubElement(lst, "{" + NS["p"] + "}sldId", {"id": str(256+i), "{" + NS["r"] + "}id": rid})
        self.parts["ppt/presentation.xml"] = serialize(root)
        self.parts[relpath("ppt/presentation.xml")] = serialize(rr)

    def bytes(self):
        out = io.BytesIO()
        with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
            for name in sorted(self.parts):
                z.writestr(name, self.parts[name])
        return out.getvalue()

    def prune(self):
        """Remove old slide graphs unreachable from the package root."""
        kept={'[Content_Types].xml'}
        def visit(part):
            if part:kept.add(part)
            rp=relpath(part)
            if rp in kept:return
            if rp in self.parts:
                kept.add(rp)
                for r in self.relations(part):
                    if r.get('TargetMode')!='External':visit(resolve(part,r.get('Target')))
        visit('')
        self.parts={k:v for k,v in self.parts.items() if k in kept}
        ct=self.root('[Content_Types].xml')
        for t in list(ct):
            if t.get('PartName') and t.get('PartName')[1:] not in self.parts:ct.remove(t)
        self.parts['[Content_Types].xml']=serialize(ct)
