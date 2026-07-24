{
  lib,
  stdenv,
  fetchurl,
  autoPatchelfHook,
  wrapGAppsHook3,
  binutils,
  alsa-lib,
  at-spi2-atk,
  at-spi2-core,
  atk,
  cairo,
  cups,
  dbus,
  expat,
  fontconfig,
  glib,
  gtk3,
  pango,
  nss,
  nspr,
  libx11,
  libxcb,
  libxcomposite,
  libxdamage,
  libxext,
  libxfixes,
  libxrandr,
  libgbm,
  libxkbcommon,
  libxshmfence,
  libdrm,
  libGL,
  vulkan-loader,
  systemd,
  pciutils,
  libnotify,
  libsecret,
}:

let
  runtimeLibraries = [
    stdenv.cc.cc.lib
    alsa-lib
    at-spi2-atk
    at-spi2-core
    atk
    cairo
    cups
    dbus
    expat
    fontconfig
    glib
    gtk3
    pango
    nss
    nspr
    libx11
    libxcb
    libxcomposite
    libxdamage
    libxext
    libxfixes
    libxrandr
    libgbm
    libxkbcommon
    libxshmfence
    libdrm
    libGL
    vulkan-loader
    systemd
    pciutils
    libnotify
    libsecret
  ];

  dynamicallyLoadedLibraryPath = lib.makeLibraryPath [
    libGL
    vulkan-loader
    libnotify
    libsecret
  ];
in
stdenv.mkDerivation (finalAttrs: {
  pname = "obsidian";
  version = "1.12.7";

  src = fetchurl {
    url = "https://github.com/obsidianmd/obsidian-releases/releases/download/v${finalAttrs.version}/obsidian-${finalAttrs.version}.tar.gz";
    hash = "sha256-/L4IsRHZwf2wm5wIlSsG4cgpxiFj66JYTEtOyFm+B50=";
  };

  nativeBuildInputs = [
    autoPatchelfHook
    wrapGAppsHook3
  ];

  nativeInstallCheckInputs = [ binutils ];
  buildInputs = runtimeLibraries;

  dontBuild = true;
  dontStrip = true;
  dontWrapGApps = true;

  installPhase = ''
    runHook preInstall

    mkdir -p \
      "$out/bin" \
      "$out/libexec/obsidian" \
      "$out/share/applications" \
      "$out/share/icons/hicolor/512x512/apps"

    cp -a . "$out/libexec/obsidian/"
    chmod a-x "$out/libexec/obsidian/"*.so* || true

    install -m 0555 ${./obsidian-wrapper.sh} "$out/bin/obsidian"
    substituteInPlace "$out/bin/obsidian" \
      --replace-fail '#!/usr/bin/env bash' '#!${stdenv.shell}' \
      --replace-fail '@out@' "$out" \
      --replace-fail '@dynamic-library-path@' '${dynamicallyLoadedLibraryPath}'

    ln -s "$out/libexec/obsidian/obsidian-cli" "$out/bin/obsidian-cli"

    install -m 0444 resources/icon.png \
      "$out/share/icons/hicolor/512x512/apps/obsidian.png"
    install -m 0444 ${./obsidian.desktop} \
      "$out/share/applications/obsidian.desktop"

    runHook postInstall
  '';

  preFixup = ''
    wrapProgram "$out/bin/obsidian" ''${gappsWrapperArgs[@]}
  '';

  doInstallCheck = true;
  installCheckPhase = ''
    runHook preInstallCheck

    test -x "$out/bin/obsidian"
    test -x "$out/bin/obsidian-cli"
    test -f "$out/libexec/obsidian/resources/app.asar"
    test -f "$out/libexec/obsidian/resources/obsidian.asar"
    test -f "$out/share/applications/obsidian.desktop"
    test -f "$out/share/icons/hicolor/512x512/apps/obsidian.png"

    elfCount=0
    while IFS= read -r -d $'\0' file; do
      if readelf -h "$file" >/dev/null 2>&1; then
        elfCount=$((elfCount + 1))
        header="$(readelf -h "$file")"
        grep -Eq 'Class:[[:space:]]+ELF64' <<< "$header"
        grep -Eq 'Data:.*little endian' <<< "$header"
        grep -Eq 'Machine:.*X86-64' <<< "$header"

        if ! lddOutput="$(ldd "$file" 2>&1)"; then
          echo "ldd could not inspect $file" >&2
          echo "$lddOutput" >&2
          exit 1
        fi
        if grep -q 'not found' <<< "$lddOutput"; then
          echo "Unresolved runtime dependency in $file" >&2
          echo "$lddOutput" >&2
          exit 1
        fi
      fi
    done < <(find "$out/libexec/obsidian" -type f -print0)

    test "$elfCount" -ge 10
    readelf -h "$out/libexec/obsidian/obsidian" >/dev/null
    readelf -h "$out/libexec/obsidian/obsidian-cli" >/dev/null

    runHook postInstallCheck
  '';

  meta = {
    description = "Knowledge base that works on top of a local folder of plain text Markdown files";
    homepage = "https://obsidian.md";
    downloadPage = "https://github.com/obsidianmd/obsidian-releases/releases";
    license = lib.licenses.obsidian;
    mainProgram = "obsidian";
    platforms = [ "x86_64-linux" ];
    sourceProvenance = with lib.sourceTypes; [ binaryNativeCode ];
  };
})
