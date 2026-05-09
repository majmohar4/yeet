# Supported file formats

yeet uses a **permissive** extension policy: any reasonably-shaped extension passes (`^\.[a-z0-9_+-]{1,12}$`), plus the named list below for known types. ClamAV scans every file regardless. Files without an extension also work.

The lists below come straight from `app/routes/upload.py` — that file is the source of truth.

## Three tiers

| Tier | What | UI | `/raw/<id>` | `/f/<id>` |
|---|---|---|---|---|
| **Safe** (~1000 named) | normal docs, media, source, archives, RAW photos, GIS data, scientific, fonts, e-books, … | no badge | served with declared MIME under strict CSP | `attachment` |
| **Web-renderable** | `.html .htm .shtml .xhtml .mhtml .svg .svgz .js .wasm` | 📄 source | served as `text/plain; charset=utf-8` so browsers show source | `attachment` |
| **Executable / script** (~70) | `.exe .msi .dll .scr .com .bat .ps1 .vbs .hta .reg .sh .so .deb .rpm .dmg .pkg .app .apk .ipa .jar .war .ear .scpt .htaccess` … | ⚠ executable | **403 — refused** | `attachment`, ClamAV-scanned |
| **Anything else** (extension-shaped) | `.zzz .0day .gen5 .x_y_z` … | no badge | served with the declared MIME under strict CSP | `attachment` |

Defence-in-depth is what keeps the permissive list safe — see [SECURITY.md → File handling](SECURITY.md#file-handling--defence-in-depth).

## Quick reference by category

### Images and graphics
- **Common raster**: `.jpg .jpeg .jfif .png .apng .gif .webp .bmp .dib .ico .icns .cur .ani`
- **HDR / next-gen**: `.avif .avifs .heic .heif .heifs .jxl .jxr .bpg .exr .hdr .pic`
- **Camera RAW**: `.cr2 .cr3 .crw .nef .nrw .arw .srf .sr2 .dng .orf .raf .rw2 .pef .ptx .srw .x3f .iiq .3fr .kdc .dcr .erf .mef .mos .mrw`
- **Vector / design**: `.svg .svgz` (web-renderable), `.eps .ps .ai .indd .idml .qxp .pub .cdr .afphoto .afdesign .afpub .sketch .fig .xd .psd .psb .xcf .kra .ora .wmf .emf`
- **Older raster**: `.tif .tiff .pcx .tga .ppm .pgm .pbm .pnm .iff .lbm .ilbm .xbm .xpm .sgi .rgb`
- **3D textures**: `.dds .ktx .ktx2 .pvr .basis .vtf`

### Documents and writing
- **PDF / page-layout**: `.pdf .xps .oxps .djvu`
- **Office word-processing**: `.doc .docx .docm .dot .dotx .dotm .odt .ott .rtf .wpd .pages`
- **Spreadsheets**: `.xls .xlsx .xlsm .xlsb .xlt .xltx .xltm .csv .tsv .ods .numbers .gnumeric .123 .wks`
- **Presentations**: `.ppt .pptx .pptm .pps .ppsx .pot .potx .odp .otp .key`
- **Markup / typeset**: `.md .markdown .rst .adoc .asciidoc .typ .tex .latex .sty .cls .bib .bst .org .opml`
- **E-books / comics**: `.epub .mobi .azw .azw3 .kfx .fb2 .lit .lrf .ibooks .acsm .cbz .cbr .cbt`
- **Plain text**: `.txt .text .log .readme .nfo .diz`

### Audio
- **Lossy**: `.mp3 .m4a .m4b .m4r .aac .ogg .oga .opus .wma .ac3 .eac3 .dts`
- **Lossless**: `.flac .wav .aiff .aif .alac .ape .wv .tta .caf`
- **MIDI**: `.mid .midi .kar .rmi`
- **Trackers / chiptune**: `.mod .s3m .xm .it .mptm .stm .669 .far .ult .okt .med .sid .nsf .vgm .vgz .gym .sap .ahx`
- **DAW / SoundFonts**: `.sf2 .sfz .sfark .gig .sbk .dls`
- **Other**: `.au .snd .ra .ram .voc .gsm .dsf .dff .amr`

### Video
- **Common**: `.mp4 .m4v .mov .avi .mkv .webm .wmv .flv .f4v`
- **MPEG / broadcast**: `.mpg .mpeg .m1v .m2v .ts .mts .m2ts .vob .ifo .mxf .gxf`
- **Mobile / web**: `.3gp .3gpp .3g2 .gifv .ogm .ogv`
- **Older / niche**: `.asf .rm .rmvb .divx .xvid .nut .nsv .y4m .yuv .swf`
- **Subtitles & lyrics**: `.srt .vtt .ass .ssa .sub .idx .sbv .ttml .smi .sami .scc .lrc .cue`

### Archives and packaging
- **Zip family**: `.zip .zipx .jar .war .ear`
- **Tar + compressors**: `.tar .gz .tgz .bz2 .tbz2 .xz .txz .zst .tzst .lz .lzma .lz4 .lzo`
- **Other archivers**: `.7z .rar .ace .arj .arc .lha .lzh .uha .sit .sitx .sea .hqx .cpio .ar`
- **Game / app data archives**: `.pak .pk3 .pk4 .pck .vpk .vsix .nupkg .gem .whl .egg .xpi .crx .phar`
- **Encoding wrappers**: `.uue .uu .mim .b64 .yenc`

### Disk / virtual machine images
`.iso .img .nrg .mds .mdf .ccd .vhd .vhdx .vmdk .vdi .qcow .qcow2 .ova .ovf .wim .esd .swm .ffu .gho .tib .v2i`

### Big-data / scientific
- **Columnar / table**: `.parquet .arrow .feather .orc .avro .hdf .hdf5 .h5 .h5ad .nc .nc4 .cdf`
- **Stats packages**: `.sav .por .dta .sas7bdat .xpt .rds .rdata .rda`
- **ML / numerics**: `.mat .npy .npz .pkl .joblib .ipynb`
- **NoSQL / dumps**: `.bson .dump .sqlite .sqlite3 .sqlitedb .duckdb .db`

### GIS / spatial / sensor data
- **Vector / metadata**: `.geojson .topojson .kml .kmz .gpx .tcx .fit .igc .shp .shx .dbf .prj .qix .cpg`
- **Tiles / raster**: `.gpkg .gdb .mbtiles .pmtiles .pbf .osm .osc`
- **Point clouds**: `.las .laz .e57 .pcd .xyz .pts`

### 3D / CAD / engineering
- **Mesh formats**: `.stl .obj .mtl .fbx .gltf .glb .draco .dae .x3d .vrml .wrl .ply`
- **Editor scenes**: `.blend .blend1 .blend2 .ma .mb .max .c4d .skp .lwo .lws .3ds .ase .x .md2 .md3 .md5mesh .md5anim .pmd .pmx .smd .vmd`
- **CAD**: `.dxf .dwg .dwf .dwfx .step .stp .iges .igs .x_t .x_b .sat .ipt .iam .ipn .sldprt .sldasm .slddrw .prt .par .psm .dft`
- **3D print / CNC**: `.3mf .amf .gcode .ngc .tap .nci .cnc .ifc .ifczip`

### Game development & mods
- **Engine assets**: `.bsp .vmf .vmt .mdl .phy .vvd .upk .uasset .umap .uplugin .uproject`
- **Godot**: `.tscn .tres .escn .gd .gdshader .godot`
- **Bethesda**: `.esm .esp .esl .bsa .ba2`
- **Ren'Py**: `.rpa .rpyc .rpym .rpymc`
- **Minecraft**: `.mcfunction .mcmeta .mcpack .mcaddon .mcworld .mctemplate .nbt .schematic .schem .litematic .mca .mcr`
- **Retro ROMs / saves**: `.nes .sfc .smc .gb .gbc .gba .nds .3dsx .n64 .v64 .z64 .gcm .wad .wbfs .nsp .xci .cia .cci .srm .gci .state .state1 .state2 .state3`

### Programming and config
- **Python / notebooks**: `.py .pyi .pyx .pxd .pxi .pyw .py3 .ipynb .rmd .qmd .myst`
- **C-family**: `.c .h .cpp .cxx .cc .c++ .hpp .hxx .h++ .ipp .tcc .inl .inc .m .mm .M .d .di .cr`
- **JVM**: `.java .class .kt .kts .scala .sc .sbt .groovy .gradle .jad`
- **JS / TS / front-end**: `.js .mjs .cjs .ts .tsx .jsx .vue .svelte .astro .marko .mdx`
- **Stylesheets**: `.css .scss .sass .less .styl .stylus .pcss`
- **Functional**: `.hs .lhs .cabal .ml .mli .mll .mly .lisp .lsp .scm .ss .rkt .clj .cljs .cljc .edn .ex .exs .erl .hrl .elm`
- **Systems / new langs**: `.rs .go .zig .nim .nims .nimble .odin .v .vh .sv .svh .vhdl .vhd .pas .pp .dpr .lpr .ada .adb .ads`
- **Scripting**: `.rb .erb .rake .gemspec .perl .pl .pm .pod .t .tcl .tk .lua .luau .raku .p6 .applescript .awk .sed`
- **Web back-end**: `.php .phtml .phps .phar .jsp .jspx .asp .aspx .cshtml .vbhtml .razor`
- **Other niche**: `.dart .swift .vala .vapi .gs .bas .vb .r .jl .f .f77 .f90 .f95 .for .ftn .asm .s .S .ld`
- **GPU shaders**: `.glsl .hlsl .wgsl .metal .frag .vert .geom .comp .tese .tesc .rgen .rmiss .rchit .rahit .shader .cginc`
- **Database / query**: `.sql .prisma .graphql .gql .cypher .cql`

### Templating & build tooling
- **Templates**: `.ejs .pug .jade .haml .slim .liquid .mustache .hbs .handlebars .twig .njk .nunjucks .jinja .jinja2 .j2 .jbuilder .rhtml`
- **Build / project**: `.cmake .cmakelists .ninja .meson .bzl .bazel .build .bp .gn .gni .am .ac .in .pc .mk .mak .makefile`
- **IDE / project files**: `.csproj .vbproj .fsproj .vcxproj .sln .pbxproj .xcodeproj .xcworkspace .xib .storyboard .nuspec`

### Config-as-code
`.json .jsonl .ndjson .json5 .jsonc .hjson .cson .xml .dtd .xsd .xsl .xslt .yaml .yml .toml .ini .cfg .conf .config .env .envrc .properties .plist .nix .dhall .hcl .tf .tfvars .tfstate .tfplan .pkr .pkrvars .sls .pp`

### Fonts
`.ttf .otf .otc .ttc .woff .woff2 .eot .pfb .pfm .pfa .afm .bdf .pcf .fnt .fon .pk .gf .tfm`

### Crypto, mail, calendars, misc
- **Hashes / signatures**: `.md5 .md5sum .sha1 .sha224 .sha256 .sha384 .sha512 .sfv .par .par2 .asc .sig .sigstore`
- **Certs / keys**: `.pem .crt .cer .der .key .pub .csr .crl .p7 .p7b .p7c .p7m .p7s .p8 .p12 .pfx .pkcs7 .pkcs8 .pkcs12 .jks .bks .keystore .pgp .gpg .kbx .ovpn .wireguard`
- **Mail**: `.eml .msg .mbox .mbx .ost .pst .nws .dbx .vmsg .oft`
- **Calendars / contacts**: `.ics .ifb .vcs .vcf .vcard .ldif`
- **Browser bits**: `.torrent .magnet .webloc .har .webmanifest`

### Executables (allowed but flagged)
- **Windows**: `.exe .msi .msu .msix .msixbundle .appx .appxbundle .dll .sys .drv .ocx .cpl .scr .com .pif .msc`
- **Windows scripting**: `.bat .cmd .ps1 .psm1 .psd1 .ps1xml .vbs .vbe .jse .wsf .wsh .hta .gadget .inf .ins .isp .reg .lnk .url`
- **Unix shells / binaries**: `.sh .bash .zsh .fish .ksh .csh .tcsh .dash .so .elf .out .run .bin`
- **Linux package formats**: `.deb .rpm .pkg .apk .ipa .appimage .flatpak .snap`
- **macOS**: `.dmg .dylib .app .scpt .scptd .workflow .action`
- **Java bytecode**: `.jar .war .ear`
- **Server-config landmines**: `.htaccess .htpasswd`

### Web-renderable (allowed; served as plain-text via `/raw/<id>`)
`.html .htm .xhtml .mhtml .shtml .svg .svgz .js .wasm`
