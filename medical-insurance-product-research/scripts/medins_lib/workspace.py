"""研究工作区：统一管理归档目录、文本缓存、标准化记录与输出。

<workspace>/
├── research.json                 项目配置与产品登记（pid → 元数据）
├── catalog_user.json             本项目新增的产品目录条目（模糊检索可命中）
├── archive/<公司>-<系列>/产品资料/<公司>_<产品>/   原始资料归档（00_资料清单.txt、00_manifest.json、01_条款…）
├── text/<pid>/<文件名>.txt        带页码标记的抽取文本（引文核验的依据）
├── records/<pid>.json            标准化字段记录（取值 + 原文引文 + 来源）
├── analysis.json                 研究者/Agent 撰写的判断（对比解读、创新点结论、结论边界）
└── outputs/                      Excel 对比表、分析报告、校验报告
"""
from __future__ import annotations

from pathlib import Path

from .catalog import make_pid
from .common import MedinsError, now_iso, read_json, safe_filename, write_json

CONFIG = "research.json"


class Workspace:
    def __init__(self, root: Path | str):
        self.root = Path(root).expanduser().resolve()

    # ---------- 路径 ----------
    @property
    def config_path(self) -> Path:
        return self.root / CONFIG

    @property
    def archive_dir(self) -> Path:
        return self.root / "archive"

    @property
    def text_root(self) -> Path:
        return self.root / "text"

    @property
    def records_dir(self) -> Path:
        return self.root / "records"

    @property
    def outputs_dir(self) -> Path:
        return self.root / "outputs"

    @property
    def analysis_path(self) -> Path:
        return self.root / "analysis.json"

    def text_dir(self, pid: str) -> Path:
        return self.text_root / pid

    def record_path(self, pid: str) -> Path:
        return self.records_dir / f"{pid}.json"

    # ---------- 配置 ----------
    def exists(self) -> bool:
        return self.config_path.exists()

    def init(self, title: str = "") -> dict:
        self.root.mkdir(parents=True, exist_ok=True)
        for d in (self.archive_dir, self.text_root, self.records_dir, self.outputs_dir):
            d.mkdir(parents=True, exist_ok=True)
        cfg = self.load(required=False) or {"schema": "medins-workspace/1", "created_at": now_iso(), "products": {}}
        if title:
            cfg["title"] = title
        cfg.setdefault("title", title or "医疗险产品研究")
        self.save(cfg)
        return cfg

    def load(self, required: bool = True) -> dict | None:
        cfg = read_json(self.config_path)
        if cfg is None and required:
            raise MedinsError(f"工作区未初始化：{self.root}（先运行 `medins.py init --workspace {self.root}`）")
        return cfg

    def save(self, cfg: dict) -> None:
        cfg["updated_at"] = now_iso()
        write_json(self.config_path, cfg)

    # ---------- 产品登记 ----------
    def products(self) -> dict[str, dict]:
        return (self.load() or {}).get("products", {})

    def register(self, meta: dict) -> dict:
        """登记产品（公司 + 产品名唯一），返回含 pid 与 archive_dir 的元数据。"""
        company, name = meta.get("company", "").strip(), meta.get("product_name", "").strip()
        if not company or not name:
            raise MedinsError("登记产品需要公司（company）与产品名称（product_name）")
        cfg = self.load()
        pid = meta.get("pid") or make_pid(company, name)
        old = cfg["products"].get(pid, {})
        series = meta.get("series") or old.get("series") or ""
        group = safe_filename(f"{company}-{series}" if series else company, 40)
        folder = safe_filename(f"{company}_{name}", 60)
        merged = {**old, **{k: v for k, v in meta.items() if v not in (None, "")}}
        merged.update({"pid": pid, "company": company, "product_name": name})
        merged.setdefault("archive_dir", f"archive/{group}/产品资料/{folder}")
        merged.setdefault("registered_at", now_iso())
        cfg["products"][pid] = merged
        self.save(cfg)
        (self.root / merged["archive_dir"]).mkdir(parents=True, exist_ok=True)
        return merged

    def get(self, pid_or_name: str) -> dict:
        prods = self.products()
        if pid_or_name in prods:
            return prods[pid_or_name]
        from .normalize import name_key
        k = name_key(pid_or_name)
        hits = [p for p in prods.values() if name_key(p["product_name"]) == k]
        if not hits:
            hits = [p for p in prods.values() if k and k in name_key(p["product_name"])]
        if len(hits) == 1:
            return hits[0]
        if not hits:
            raise MedinsError(f"工作区中没有产品：{pid_or_name}（`medins.py list` 查看已登记产品）")
        raise MedinsError(f"“{pid_or_name}”匹配到多个产品：" + "；".join(f"{h['pid']}={h['product_name']}" for h in hits))

    def product_dir(self, pid: str) -> Path:
        return self.root / self.get(pid)["archive_dir"]

    def update_product(self, pid: str, **fields) -> dict:
        cfg = self.load()
        cfg["products"][pid].update({k: v for k, v in fields.items() if v is not None})
        self.save(cfg)
        return cfg["products"][pid]
