import logging
import mimetypes
import re
import uuid
from datetime import datetime, timedelta, timezone

import bcrypt
from fastapi import APIRouter, File, Form, Request, UploadFile
from fastapi.responses import JSONResponse, PlainTextResponse
from fastapi.templating import Jinja2Templates

from app.database import get_db
from app.middleware.rate_limit import (
    _client_ip,
    check_upload_bandwidth,
    record_bandwidth,
    validate_bypass_code,
)
from app.services import concurrent, storage, virus_scan
from app.services.config_manager import (
    get_file_expiry_hours,
    get_max_file_size,
    get_storage_limit,
)

logger = logging.getLogger("yeet.upload")
router = APIRouter()
templates = Jinja2Templates(directory="templates")

# ── Extension policy ─────────────────────────────────────────────────────────
# Safety model:
#   1. ClamAV scans every upload.
#   2. /f/{id} forces Content-Disposition: attachment + X-Content-Type-Options:
#      nosniff — files never render inline, even HTML/SVG.
#   3. /raw/{id} neutralizes web-renderable types (text/plain) and refuses
#      executables. So even though we accept .exe / .html, neither can run or
#      render in the browser via our preview path.
#   4. The UI shows a warning badge on dangerous types so downloaders know.
#
# Filenames without an extension are treated as ".bin" (still scanned).

# Files that are safe to share. The list is exhaustive because users like to
# see "yes, that's a known thing" — but the permissive fallback below also
# catches anything that just *looks* like an extension. The distinction matters
# only for the UI (we don't badge unknowns) and documentation.
SAFE_EXTENSIONS = {
    # ── Raster images ────────────────────────────────────────────────────
    ".jpg", ".jpeg", ".jpe", ".jfif", ".jif", ".pjpeg", ".pjp",
    ".png", ".apng", ".gif", ".webp", ".bmp", ".dib",
    ".ico", ".cur", ".ani", ".icns",
    ".avif", ".avifs", ".heic", ".heics", ".heif", ".heifs",
    ".tif", ".tiff", ".jp2", ".j2k", ".jpf", ".jpx", ".jpm", ".mj2",
    ".jxl", ".jxr", ".wdp", ".hdp", ".bpg",
    ".ppm", ".pgm", ".pbm", ".pnm", ".pam", ".pfm",
    ".tga", ".targa", ".pcx", ".sgi", ".rgb", ".rgba", ".ras",
    ".xbm", ".xpm", ".pcd",
    ".iff", ".lbm", ".ilbm",
    ".exr", ".hdr", ".pic", ".rgbe",
    ".dds", ".ktx", ".ktx2", ".pvr", ".basis", ".vtf",
    # ── Camera RAW ───────────────────────────────────────────────────────
    ".cr2", ".cr3", ".crw", ".nef", ".nrw", ".arw", ".srf", ".sr2",
    ".dng", ".orf", ".raf", ".rw2", ".rwl", ".pef", ".ptx", ".srw",
    ".x3f", ".iiq", ".3fr", ".kdc", ".dcr", ".erf", ".mef", ".mos",
    ".mrw", ".raw", ".rwz",
    # ── Vector / design ─────────────────────────────────────────────────
    ".eps", ".epsi", ".epsf", ".ps", ".prn",
    ".ai", ".indd", ".idml", ".qxp", ".pub", ".cdr",
    ".afphoto", ".afdesign", ".afpub", ".sketch", ".fig", ".xd",
    ".psd", ".psb", ".xcf", ".kra", ".ora",
    ".wmf", ".emf", ".emz", ".wmz",
    # ── Documents ────────────────────────────────────────────────────────
    ".pdf", ".xps", ".oxps",
    ".txt", ".text", ".md", ".markdown", ".mdown", ".mkd", ".mdwn", ".mdtxt",
    ".rst", ".restructuredtext", ".adoc", ".asciidoc", ".typ",
    ".pod", ".rdoc", ".texi", ".info", ".man",
    ".doc", ".docx", ".docm", ".dot", ".dotx", ".dotm",
    ".odt", ".ott", ".oth", ".otm",
    ".rtf", ".rtfd", ".wpd", ".wp", ".wp4", ".wp5", ".wp6", ".wpt",
    ".pages", ".tex", ".latex", ".sty", ".cls", ".bib", ".bst", ".aux",
    ".log", ".readme", ".nfo", ".diz",
    ".djvu", ".djv",
    ".one", ".onetoc2", ".onepkg",
    ".org", ".opml",
    # ── Spreadsheets ─────────────────────────────────────────────────────
    ".xls", ".xlsx", ".xlsm", ".xlsb", ".xlt", ".xltx", ".xltm",
    ".csv", ".tsv", ".dsv",
    ".ods", ".ots", ".numbers", ".gnumeric", ".gnm",
    ".wks", ".wk1", ".wk3", ".wk4", ".wk5", ".123",
    # ── Presentations ────────────────────────────────────────────────────
    ".ppt", ".pptx", ".pptm", ".pps", ".ppsx", ".ppsm",
    ".pot", ".potx", ".potm",
    ".odp", ".otp", ".key",
    # ── E-books ──────────────────────────────────────────────────────────
    ".epub", ".mobi", ".azw", ".azw3", ".azw4", ".kfx",
    ".fb2", ".fbz", ".lit", ".lrf", ".pdb", ".prc", ".tcr",
    ".ibooks", ".acsm", ".cbz", ".cbr", ".cbt", ".cba", ".cb7",
    # ── Subtitles & lyrics ──────────────────────────────────────────────
    ".srt", ".vtt", ".ass", ".ssa", ".sub", ".idx", ".sbv",
    ".dfxp", ".ttml", ".smi", ".sami", ".scc", ".mcc", ".itt",
    ".pjs", ".rt", ".usf", ".lrc", ".cue", ".toc",
    # ── Archives & packaging ────────────────────────────────────────────
    ".zip", ".zipx", ".jar", ".war", ".ear",
    ".tar", ".gz", ".tgz", ".taz", ".tz",
    ".bz2", ".tbz", ".tbz2",
    ".xz", ".txz", ".lzma", ".tlz",
    ".zst", ".zstd", ".tzst",
    ".lz", ".lz4", ".lzo", ".lha", ".lzh",
    ".7z", ".7zip", ".rar", ".ace", ".arj", ".arc",
    ".cab", ".uha", ".sit", ".sitx", ".sea", ".hqx",
    ".cpio", ".ar", ".pak", ".pk3", ".pk4", ".pck", ".vpk", ".vsix",
    ".nupkg", ".gem", ".whl", ".egg", ".xpi", ".crx", ".phar",
    ".uue", ".uu", ".mim", ".b64", ".yenc",
    # ── Disk images ─────────────────────────────────────────────────────
    ".iso", ".img", ".bin", ".cdr", ".nrg", ".mds", ".mdf", ".ccd",
    ".vhd", ".vhdx", ".vmdk", ".vdi", ".qcow", ".qcow2", ".ova", ".ovf",
    ".wim", ".esd", ".swm", ".ffu", ".gho", ".tib", ".v2i",
    # ── Video ───────────────────────────────────────────────────────────
    ".mp4", ".m4v", ".m4p", ".mov", ".qt",
    ".avi", ".mkv", ".mka", ".webm",
    ".flv", ".f4v", ".f4p", ".f4a", ".f4b",
    ".mpg", ".mpeg", ".mpe", ".mpv", ".m1v", ".m2v", ".mp2", ".mp4v",
    ".3gp", ".3gpp", ".3g2", ".3gpp2",
    ".mts", ".m2ts", ".ts", ".vob", ".ifo", ".bup",
    ".asf", ".wmv", ".rm", ".rmvb", ".divx", ".xvid",
    ".mxf", ".gxf", ".lxf", ".nut", ".ogm", ".ogv",
    ".y4m", ".yuv", ".nsv", ".dv", ".dvr-ms", ".swf", ".gifv",
    # ── Audio ───────────────────────────────────────────────────────────
    ".mp3", ".m4a", ".m4b", ".m4r", ".aac",
    ".wav", ".wave", ".flac", ".ogg", ".oga", ".opus",
    ".aiff", ".aif", ".aifc", ".caf", ".amr",
    ".wma", ".ape", ".wv", ".tta", ".ac3", ".eac3",
    ".dts", ".dtshd", ".dtsma", ".alac",
    ".mid", ".midi", ".kar", ".rmi",
    ".au", ".snd", ".ra", ".ram",
    ".voc", ".gsm", ".dsf", ".dff",
    # Tracker & chiptune
    ".mod", ".s3m", ".xm", ".it", ".mptm", ".mtm", ".stm",
    ".669", ".far", ".ult", ".okt", ".med", ".ams", ".dbm",
    ".mt2", ".psm", ".ptm", ".umx", ".imf",
    ".sid", ".nsf", ".nsfe", ".vgm", ".vgz", ".gym", ".sap", ".ahx",
    # SoundFonts / DAW
    ".sf2", ".sfz", ".sfark", ".gig", ".sbk", ".dls",
    # ── Fonts ───────────────────────────────────────────────────────────
    ".ttf", ".otf", ".otc", ".ttc", ".woff", ".woff2", ".eot",
    ".pfb", ".pfm", ".afm", ".bdf", ".pcf", ".fnt", ".fon",
    ".pfa", ".pk", ".gf", ".tfm",
    # ── Data / config ───────────────────────────────────────────────────
    ".json", ".jsonl", ".jsonc", ".json5", ".ndjson", ".hjson", ".cson",
    ".xml", ".dtd", ".xsd", ".xsl", ".xslt", ".rss", ".atom",
    ".yaml", ".yml", ".toml",
    ".ini", ".cfg", ".conf", ".config", ".properties", ".plist",
    ".env", ".envrc",
    ".gitignore", ".gitattributes", ".gitmodules", ".gitconfig",
    ".gitkeep", ".gitlab-ci",
    ".dockerignore", ".editorconfig", ".eslintrc", ".prettierrc",
    ".babelrc", ".browserslistrc", ".npmrc", ".yarnrc", ".nvmrc",
    ".lock", ".sum", ".mod", ".work",
    ".manifest", ".props", ".targets", ".rules",
    ".csv", ".tsv", ".jsonl",  # already above; harmless dupes
    # Modern config-as-code
    ".nix", ".dhall", ".hcl", ".tf", ".tfvars", ".tfstate", ".tfplan",
    ".pkr", ".pkrvars", ".sls", ".pp",
    # ── Spatial / GIS ───────────────────────────────────────────────────
    ".geojson", ".topojson", ".kml", ".kmz",
    ".gpx", ".tcx", ".fit", ".igc",
    ".shp", ".shx", ".dbf", ".prj", ".qix", ".cpg", ".sbn", ".sbx",
    ".gpkg", ".gdb", ".mbtiles", ".pmtiles", ".pbf", ".osm", ".osc",
    ".las", ".laz", ".e57", ".pcd",
    # ── Big data / scientific ───────────────────────────────────────────
    ".parquet", ".arrow", ".feather", ".orc", ".avro",
    ".hdf", ".hdf5", ".h5", ".h5ad", ".nc", ".nc4", ".cdf",
    ".sav", ".por", ".dta", ".sas7bdat", ".sas7bcat", ".xpt",
    ".mat", ".npy", ".npz", ".pkl", ".joblib",
    ".rds", ".rdata", ".rda",
    ".bson", ".dump", ".sqlitedb",
    # ── 3D models / CAD ─────────────────────────────────────────────────
    ".stl", ".obj", ".mtl", ".fbx", ".gltf", ".glb", ".draco",
    ".blend", ".blend1", ".blend2", ".blend11",
    ".dae", ".x3d", ".vrml", ".wrl", ".ply",
    ".ma", ".mb", ".max", ".c4d", ".skp", ".lwo", ".lws",
    ".3ds", ".ase", ".x", ".md2", ".md3", ".md5mesh", ".md5anim",
    ".smd", ".vmd", ".pmd", ".pmx", ".mqo", ".mqoz", ".b3d",
    ".dxf", ".dwg", ".dwf", ".dwfx",
    ".step", ".stp", ".iges", ".igs", ".x_t", ".x_b", ".sat",
    ".ipt", ".iam", ".ipn", ".sldprt", ".sldasm", ".slddrw",
    ".prt", ".par", ".psm", ".dft",
    ".3mf", ".amf", ".gcode", ".ngc", ".tap", ".nci", ".cnc",
    ".ifc", ".ifczip", ".xyz", ".pts",
    # ── Game engines / Minecraft / mods ────────────────────────────────
    ".bsp", ".vmf", ".vmt", ".mdl", ".phy", ".vvd",
    ".upk", ".uasset", ".umap", ".uplugin", ".uproject",
    ".tscn", ".tres", ".escn", ".gd", ".gdshader", ".godot",
    ".esm", ".esp", ".esl", ".bsa", ".ba2",
    ".rpa", ".rpyc", ".rpymc", ".rpym",
    ".mcfunction", ".mcmeta", ".mcpack", ".mcaddon", ".mcworld",
    ".mctemplate", ".nbt", ".schematic", ".schem", ".litematic",
    ".mca", ".mcr",
    # ── Retro ROMs & saves (data only) ─────────────────────────────────
    ".nes", ".sfc", ".smc", ".unf",
    ".gb", ".gbc", ".gba", ".nds", ".3dsx", ".cia", ".cci",
    ".n64", ".v64", ".z64", ".gcm", ".wad", ".wbfs",
    ".nsp", ".xci",
    ".srm", ".gci", ".state", ".state1", ".state2", ".state3",
    # ── Programming languages (extended) ───────────────────────────────
    ".py", ".pyi", ".pyx", ".pxd", ".pxi", ".pyw", ".py3",
    ".ipynb", ".rmd", ".qmd", ".myst",
    ".java", ".class", ".jad",
    ".kt", ".kts", ".scala", ".sc", ".sbt", ".groovy", ".gradle",
    ".c", ".h", ".cpp", ".cxx", ".cc", ".c++",
    ".hpp", ".hxx", ".h++", ".ipp", ".tcc", ".inl", ".inc",
    ".m", ".mm", ".M",
    ".rs", ".go", ".d", ".di", ".cr",
    ".js", ".mjs", ".cjs", ".ts", ".tsx", ".jsx",
    ".vue", ".svelte", ".astro", ".marko", ".mdx",
    ".css", ".scss", ".sass", ".less", ".styl", ".stylus", ".pcss",
    ".sql", ".prisma", ".graphql", ".gql",
    ".cypher", ".cql",
    ".rb", ".erb", ".rake", ".gemspec",
    ".php", ".phtml", ".phps", ".phar",
    ".swift", ".dart",
    ".lua", ".luau",
    ".r", ".jl", ".tcl", ".tk",
    ".clj", ".cljs", ".cljc", ".edn",
    ".ex", ".exs", ".erl", ".hrl", ".elm",
    ".hs", ".lhs", ".cabal",
    ".ml", ".mli", ".mll", ".mly",
    ".lisp", ".lsp", ".scm", ".ss", ".sld", ".rkt", ".cl",
    ".raku", ".rakumod", ".rakudoc", ".p6", ".pl6", ".pm6",
    ".zig", ".nim", ".nims", ".nimble", ".odin", ".v",
    ".vh", ".sv", ".svh", ".sva", ".vhdl", ".vhd",
    ".pas", ".pp", ".dpr", ".lpr", ".inc",
    ".ada", ".adb", ".ads", ".gpr",
    ".vala", ".vapi", ".gs",
    ".bas", ".vb", ".vbs",  # vbs already executable; harmless to dup-list
    ".pl", ".pm", ".perl", ".pod", ".t",
    ".f", ".f77", ".f90", ".f95", ".f03", ".f08", ".for", ".ftn",
    ".asm", ".s", ".S", ".inc", ".ld",
    ".awk", ".sed",
    ".applescript",
    # GPU / shaders
    ".hlsl", ".glsl", ".frag", ".vert", ".geom", ".comp",
    ".tese", ".tesc", ".rgen", ".rmiss", ".rchit", ".rahit",
    ".metal", ".wgsl", ".shader", ".cginc",
    # ── Templating & view layers ────────────────────────────────────────
    ".ejs", ".pug", ".jade", ".haml", ".slim",
    ".liquid", ".mustache", ".hbs", ".handlebars",
    ".twig", ".njk", ".nunjucks", ".jinja", ".jinja2", ".j2",
    ".jbuilder", ".rhtml",
    ".jsp", ".jspx", ".asp", ".aspx",
    ".cshtml", ".vbhtml", ".razor",
    # ── Build / project files ───────────────────────────────────────────
    ".cmake", ".cmakelists", ".ninja", ".meson", ".bzl", ".bazel",
    ".build", ".bp", ".gn", ".gni",
    ".am", ".ac", ".in", ".pc", ".mk", ".mak", ".makefile",
    ".csproj", ".vbproj", ".fsproj", ".vcxproj", ".sln",
    ".pbxproj", ".xcodeproj", ".xcworkspace", ".xib", ".storyboard",
    ".nuspec", ".nupkg",
    # ── Docs / source-misc ──────────────────────────────────────────────
    ".diff", ".patch", ".rej", ".orig",
    ".sample", ".example", ".template", ".tpl", ".tmpl",
    # ── OS metadata & misc bits people share ───────────────────────────
    ".ds_store", ".thumbs", ".db", ".sqlite", ".sqlite3", ".duckdb",
    ".bak", ".old", ".tmp", ".swp", ".swo", ".swn",
    ".part", ".partial", ".crdownload",
    # ── Hash / signature manifests ─────────────────────────────────────
    ".md5", ".md5sum", ".sha1", ".sha224", ".sha256", ".sha384", ".sha512",
    ".sfv", ".par", ".par2", ".asc", ".sig", ".sigstore",
    # ── PKI / crypto material ──────────────────────────────────────────
    ".pem", ".crt", ".cer", ".der", ".key", ".pub", ".csr", ".crl",
    ".p7", ".p7b", ".p7c", ".p7m", ".p7s", ".p8", ".p12", ".pfx",
    ".pkcs7", ".pkcs8", ".pkcs12", ".jks", ".bks", ".keystore",
    ".pgp", ".gpg", ".kbx", ".gpgsig",
    ".ovpn", ".wireguard", ".conf",
    # ── Mail / calendar / contacts ─────────────────────────────────────
    ".eml", ".msg", ".mbox", ".mbx", ".ost", ".pst", ".nws", ".dbx",
    ".vmsg", ".oft",
    ".ics", ".ifb", ".vcs",
    ".vcf", ".vcard", ".ldif",
    # ── Browser / web misc ─────────────────────────────────────────────
    ".torrent", ".magnet", ".webloc",
    ".har", ".webmanifest", ".webp",
    # ── Misc useful bits ───────────────────────────────────────────────
    ".dockerfile", ".containerfile",
    ".pem", ".tar.gz", ".tar.bz2", ".tar.xz", ".tar.zst",  # composite — harmless
    ".log", ".txt",  # repeated; sets dedupe
}

# Web-renderable types — accepted but `/raw/{id}` serves them as text/plain
# (so HTML doesn't render and SVG can't run scripts even with our CSP).
WEB_RENDERABLE_EXTENSIONS = {
    ".html", ".htm", ".xhtml", ".mhtml", ".shtml",
    ".svg", ".svgz",
    ".js", ".wasm",
}

# Executables / scripts — accepted but flagged dangerous.
# `/raw/{id}` refuses these outright; only `/f/{id}` (forced attachment) works.
EXECUTABLE_EXTENSIONS = {
    # Windows binaries / installers
    ".exe", ".msi", ".msu", ".msix", ".msixbundle", ".appx", ".appxbundle",
    ".dll", ".sys", ".drv", ".ocx", ".cpl", ".scr", ".com", ".pif", ".msc",
    # Windows scripting
    ".bat", ".cmd", ".ps1", ".psm1", ".psd1", ".ps1xml",
    ".vbs", ".vbe", ".jse", ".wsf", ".wsh",
    ".hta", ".gadget", ".inf", ".ins", ".isp", ".reg",
    ".lnk", ".url", ".pcd",
    # Unix shells / binaries
    ".sh", ".bash", ".zsh", ".fish", ".ksh", ".csh", ".tcsh", ".dash",
    ".so", ".elf", ".out", ".run", ".bin",
    ".deb", ".rpm", ".pkg", ".apk", ".ipa",
    ".appimage", ".flatpak", ".snap",
    # macOS
    ".dmg", ".dylib", ".app", ".scpt", ".scptd", ".workflow", ".action",
    # Java / cross-platform bytecode
    ".jar", ".war", ".ear",
    # Server / remote-config that can backdoor a host
    ".htaccess", ".htpasswd",
}

ALLOWED_EXTENSIONS = SAFE_EXTENSIONS | WEB_RENDERABLE_EXTENSIONS | EXECUTABLE_EXTENSIONS


def _file_ext(filename: str) -> str:
    """Lowercase extension with leading dot, e.g. '.html'. '' for none."""
    if not filename or "." not in filename:
        return ""
    return "." + filename.rsplit(".", 1)[-1].lower()


def is_dangerous_ext(filename: str) -> bool:
    return _file_ext(filename) in EXECUTABLE_EXTENSIONS


def is_web_renderable_ext(filename: str) -> bool:
    return _file_ext(filename) in WEB_RENDERABLE_EXTENSIONS


_PERMISSIVE_EXT_RE = re.compile(r"^\.[a-z0-9_+-]{1,12}$")


def _check_extension(filename: str) -> str | None:
    """Return error string if extension is unacceptably weird, else None.

    Three tiers:
      1. Known-safe / known-web / known-dangerous → accepted (with the right
         badge applied later).
      2. Unknown but extension-shaped (1–12 alphanumerics, hyphens or
         underscores) → accepted silently. ClamAV still scans, `/f/` forces
         attachment, `/raw/` neutralizes script-y mimes — so an unknown
         extension can't run code or render dangerously through us.
      3. Anything else (no dot, weird characters, absurdly long) → rejected.

    Files without an extension are accepted (no security signal in the
    absence itself).
    """
    if "." not in (filename or ""):
        return None
    ext = _file_ext(filename)
    if ext in ALLOWED_EXTENSIONS:
        return None
    if _PERMISSIVE_EXT_RE.match(ext):
        return None
    return (
        f"Extension '{ext}' looks malformed (too long or unusual characters). "
        "Pack the file into a .zip archive instead."
    )


@router.get("/")
async def index(request: Request):
    total = storage.get_total_storage()
    limit = get_storage_limit()
    pct = round(total / limit * 100, 1) if limit else 0
    return templates.TemplateResponse(
        "index.html",
        {
            "request": request,
            "storage_pct": pct,
            "storage_warning": pct >= 80,
            "storage_blocked": pct >= 90,
            "max_file_mb": get_max_file_size() // 1_048_576,
            "expiry_hours": get_file_expiry_hours(),
        },
    )


@router.post("/upload")
async def upload_file(
    request: Request,
    file: UploadFile = File(...),
    password: str = Form(default=""),
    max_downloads: str = Form(default=""),
    bypass_code: str = Form(default=""),
    session_id: str = Form(default=""),
    expiry_hours: str = Form(default=""),
    burn: str = Form(default=""),
    website: str = Form(default=""),  # honeypot
):
    ip = _client_ip(request)
    ua = request.headers.get("user-agent", "")

    # Honeypot: real browsers leave this blank
    if website:
        logger.warning("honeypot triggered: ip=%s", ip)
        await _audit(ip, ua, "bot_rejected", None, f"honeypot={website[:32]}")
        return JSONResponse({"error": "Invalid request."}, status_code=400)

    max_file = get_max_file_size()
    storage_limit = get_storage_limit()

    # ── Bypass code ───────────────────────────────────────────────────────────
    bypassed = await validate_bypass_code(bypass_code)

    # ── Concurrent upload limit ───────────────────────────────────────────────
    if not bypassed:
        if not await concurrent.acquire(ip):
            return JSONResponse(
                {"error": "Please wait for your current uploads to finish (max 2 at once)."},
                status_code=429,
            )
    else:
        await concurrent.acquire(ip)  # still track so release works

    try:
        return await _do_upload(
            request, file, password, max_downloads,
            ip, ua, max_file, storage_limit, bypassed, session_id, expiry_hours,
            burn,
        )
    finally:
        await concurrent.release(ip)


async def _do_upload(
    request, file, password, max_downloads,
    ip, ua, max_file, storage_limit, bypassed, session_id="", expiry_hours="",
    burn="",
):
    burn_flag = 1 if str(burn).strip() in ("1", "true", "on", "yes") else 0
    # ── File size check (before reading full body to fail fast) ───────────────
    content_length = int(request.headers.get("content-length", 0))
    if not bypassed and content_length > max_file:
        return JSONResponse(
            {"error": f"File too large. Maximum is {max_file // 1_048_576} MB."},
            status_code=413,
        )

    # ── Storage limit pre-check at 90% ───────────────────────────────────────
    total_used = storage.get_total_storage()
    if not bypassed and total_used >= storage_limit * 0.9:
        return JSONResponse(
            {"error": "Storage limit reached. Contact info@majmohar.eu"},
            status_code=507,
        )

    # ── Read file ─────────────────────────────────────────────────────────────
    data = await file.read(max_file + 1)
    if not bypassed and len(data) > max_file:
        return JSONResponse(
            {"error": f"File too large. Maximum is {max_file // 1_048_576} MB."},
            status_code=413,
        )

    # ── Upload bandwidth check (byte-based, 24h rolling) ─────────────────────
    if not bypassed:
        bw = await check_upload_bandwidth(ip, len(data))
        if not bw["allowed"]:
            hrs = bw["reset_in_hours"]
            return JSONResponse(
                {
                    "error": (
                        f"Daily upload limit reached "
                        f"({bw['limit'] // 1_048_576} MB/day). "
                        f"Resets in {hrs:.1f}h."
                    ),
                    "bandwidth": bw,
                },
                status_code=429,
                headers={"Retry-After": str(int(hrs * 3600))},
            )

    # ── Extension whitelist ───────────────────────────────────────────────────
    ext_error = _check_extension(file.filename or "")
    if ext_error:
        return JSONResponse({"error": ext_error}, status_code=400)

    # ── Virus scan ────────────────────────────────────────────────────────────
    scan_status, threat = virus_scan.scan_bytes(data)
    if scan_status == "infected":
        orig = file.filename or "unknown"
        logger.warning("virus detected: ip=%s file=%s threat=%s", ip, orig, threat)
        await _audit(ip, ua, "virus_detected", None, f"filename:{orig},threat:{threat}")
        return JSONResponse(
            {"error": f"File rejected: malware detected ({threat})."},
            status_code=422,
        )

    # ── Save file ─────────────────────────────────────────────────────────────
    file_id = uuid.uuid4().hex
    safe_name = storage.sanitize_filename(file.filename or "file")
    file_hash = storage.hash_bytes(data)
    mime_type = mimetypes.guess_type(safe_name)[0] or "application/octet-stream"
    await storage.save_file(file_id, data)

    password_hash = None
    if password:
        password_hash = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()

    expiry_h = get_file_expiry_hours()
    if expiry_hours.strip().isdigit():
        requested = int(expiry_hours.strip())
        if 1 <= requested <= 168:
            expiry_h = requested
    expires_at = (datetime.now(timezone.utc) + timedelta(hours=expiry_h)).isoformat()

    max_dl = None
    if max_downloads.strip().isdigit():
        max_dl = int(max_downloads.strip())
    if burn_flag:
        # Burn-after-read implies single download. If the user also set a
        # different cap, the smaller wins.
        max_dl = 1 if max_dl is None else min(max_dl, 1)

    db = await get_db()
    safe_session = session_id.strip()[:64] if session_id else None
    await db.execute(
        """INSERT INTO files
           (id, filename, orig_name, file_hash, file_size, mime_type,
            password_hash, expires_at, max_downloads, client_ip, scan_status,
            uploader_session, burn_after_read)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            file_id, safe_name, file.filename or "file", file_hash,
            len(data), mime_type, password_hash, expires_at,
            max_dl, ip, scan_status, safe_session, burn_flag,
        ),
    )
    await db.commit()

    # ── Record bandwidth (even for bypassed uploads) ──────────────────────────
    await record_bandwidth(ip, len(data))
    await _audit(ip, ua, "upload", file_id,
                 f"size:{len(data)},scan:{scan_status},bypassed:{bypassed}")

    # ── Warnings in response ──────────────────────────────────────────────────
    bw_info = await check_upload_bandwidth(ip, 0)
    warning = None
    if not bypassed and bw_info["pct"] >= 80:
        warning = (
            f"You've used {bw_info['pct']}% of your daily upload limit "
            f"({bw_info['limit'] // 1_048_576} MB). "
            f"Resets in {bw_info['reset_in_hours']:.1f}h."
        )
    # Dangerous-type heads-up (informational, not a block)
    dangerous_warning = None
    if is_dangerous_ext(file.filename or ""):
        dangerous_warning = (
            "This file is an executable or script. Recipients should "
            "only run it if they trust the source."
        )
    elif is_web_renderable_ext(file.filename or ""):
        dangerous_warning = (
            "This file can be rendered by browsers (HTML/SVG/JS). "
            "It will only be downloaded, never run, on yeet."
        )

    download_url = str(request.base_url).rstrip("/") + f"/f/{file_id}"
    return JSONResponse(
        {
            "id": file_id,
            "url": download_url,
            "expires_at": expires_at,
            "size": len(data),
            "scan_status": scan_status,
            "bypassed": bypassed,
            "warning": warning,
            "dangerous": is_dangerous_ext(file.filename or ""),
            "advisory": dangerous_warning,
        },
        status_code=201,
    )


@router.post("/raw")
async def upload_raw(
    request: Request,
    f: UploadFile = File(...),
    burn: str = Form(default=""),
    expiry_hours: str = Form(default=""),
    bypass_code: str = Form(default=""),
):
    """curl-friendly: `curl -F f=@thing.txt https://yeet/raw` → URL on stdout."""
    ip = _client_ip(request)
    ua = request.headers.get("user-agent", "")
    bypassed = await validate_bypass_code(bypass_code)

    if not bypassed:
        if not await concurrent.acquire(ip):
            return PlainTextResponse(
                "error: too many concurrent uploads (max 2)\n", status_code=429
            )
    else:
        await concurrent.acquire(ip)

    try:
        result = await _do_upload(
            request, f, "", "", ip, ua,
            get_max_file_size(), get_storage_limit(),
            bypassed, "", expiry_hours, burn,
        )
    finally:
        await concurrent.release(ip)

    if result.status_code != 201:
        try:
            import json
            body = json.loads(result.body.decode())
            return PlainTextResponse(
                f"error: {body.get('error', 'upload failed')}\n",
                status_code=result.status_code,
            )
        except Exception:
            return PlainTextResponse("error: upload failed\n", status_code=result.status_code)

    import json
    body = json.loads(result.body.decode())
    return PlainTextResponse(body["url"] + "\n", status_code=201)


async def _audit(ip, ua, action, file_id, details=None):
    db = await get_db()
    await db.execute(
        "INSERT INTO audit_log (action, file_id, client_ip, user_agent, details) VALUES (?,?,?,?,?)",
        (action, file_id, ip, ua, details),
    )
    await db.commit()
