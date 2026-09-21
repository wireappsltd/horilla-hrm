#!/usr/bin/env bash
# ============================================================================
#  worm-guard.sh — DETECT + (optional) CLEAN the config-injection npm worm
#  Covers IOCs from github.com/orgs/community/discussions/188732
#  (config-injection / postcss-next worm; folderOpen VS Code variant;
#  trojanized-npm; C2 166.88.54.158).
#
#  Generic markers (catch ALL campaign variants, A4-1928 / A8-3967 / etc):
#    global['!']=   global['_V']=   global['_t_t']   _$_xxxx (hex)
#    100+ spaces followed by code on one line (hidden-payload trick)
#    TRON/BSC blockchain C2 strings inside config files
#
#  OS NOTE: written for Windows Git Bash (MINGW) + macOS/Linux. Paths use $HOME
#  so it adapts automatically. If anything is off for your OS, paste this file
#  into an AI assistant and ask it to adjust for your platform — keep the scan
#  logic and signatures identical, only change paths/shell builtins.
#
#  SAFE BY DESIGN:
#    - Never runs node / npm / npx
#    - Scan phase is 100% read-only
#    - Cleaning only happens after you type "y" for each category
#    - Every modified file gets a backup:  <file>.worm-infected.bak
#    - Removed files go to quarantine (~/.worm-quarantine/<timestamp>/),
#      nothing is permanently deleted
#
#  Usage:
#    bash worm-guard.sh ~/projects ~/Downloads     # scan these dirs + system
#    bash worm-guard.sh .                          # scan current dir + system
#    bash worm-guard.sh ~/projects --scan-only     # never modify anything
#    bash worm-guard.sh ~/projects --yes           # clean without prompting
# ============================================================================

RED=$'\033[31m'; GRN=$'\033[32m'; YEL=$'\033[33m'; BLD=$'\033[1m'; RST=$'\033[0m'
HITS=0; CLEANED=0; SCAN_ONLY=0; AUTO_YES=0; DIRS=()
QDIR="$HOME/.worm-quarantine/$(date +%Y%m%d-%H%M%S)"

FULL=0
for a in "$@"; do
  case "$a" in
    --scan-only) SCAN_ONLY=1 ;;
    --yes)       AUTO_YES=1 ;;
    --full)      FULL=1 ;;
    *)           [ -d "$a" ] && DIRS+=("$(cd "$a" && pwd)") || { echo "skip (not a dir): $a"; } ;;
  esac
done
if [ "$FULL" = 1 ]; then
  # whole home, one top-level folder at a time (so you see progress),
  # skipping ~/Library (app internals; the worm's persistence there is
  # covered by the dedicated system checks below, which look at the exact
  # paths it uses) — walking all of ~/Library is what makes scans hang.
  DIRS=()
  for d in "$HOME"/*/; do
    base=$(basename "$d")
    case "$base" in Library) continue ;; esac
    DIRS+=("${d%/}")
  done
  echo "${BLD}Full-Mac mode: scanning ${#DIRS[@]} home folders one by one:${RST}"
  printf '  %s\n' "${DIRS[@]}"
fi
[ ${#DIRS[@]} -eq 0 ] && DIRS=("$(pwd)")

flag(){ HITS=$((HITS+1)); echo "${RED}${BLD}[INFECTED]${RST} $*"; }
warn(){ echo "${YEL}[review]${RST} $*"; }
ok(){   echo "${GRN}[clean]${RST} $*"; }
ask(){
  [ "$SCAN_ONLY" = 1 ] && return 1
  [ "$AUTO_YES" = 1 ] && return 0
  printf "${YEL}%s [y/N] ${RST}" "$1"
  read -r ans </dev/tty 2>/dev/null || read -r ans
  [ "$ans" = "y" ] || [ "$ans" = "Y" ]
}
quarantine(){ mkdir -p "$QDIR"; mv "$1" "$QDIR/$(basename "$1").$(date +%s)" && CLEANED=$((CLEANED+1)) && echo "    -> quarantined to $QDIR"; }

# Perl program: cut file at the first worm marker, strip the hidden
# whitespace run before it, remove injected createRequire lines.
# (modified for markdown visualization safely)
PERL_CLEAN='
my $f = $ARGV[0];
open my $in, "<", $f or exit 1; local $/; my $s = <$in>; close $in;
my @pos;
for my $re (qr/global\[\x27!\x27\]=/, qr/global\[\x27_V\x27\]=/, qr/global\[\x27_t_t\x27\]/, qr/_\$_[0-9a-f]{4}/, qr/ {100,}(?=\S)/) {
  push @pos, $-[0] if $s =~ $re;
}
exit 2 unless @pos;
my $cut = (sort { $a <=> $b } @pos)[0];
my $clean = substr($s, 0, $cut);
$clean =~ s/[ \t;]+\z//s;                                  # hidden space run
$clean =~ s/^import\s*\{\s*createRequire\s*\}\s*from\s*[\x27"](node:)?module[\x27"];?[ \t]*\n//mg;
$clean =~ s/^(const|var|let)\s+require\s*=\s*createRequire\(import\.meta\.url\);?[ \t]*\n//mg;
$clean .= "\n" unless $clean =~ /\n\z/;
open my $out, ">", $f or exit 1; print $out $clean; close $out;
exit 0;
'
clean_config(){
  cp "$1" "$1.worm-infected.bak"
  if perl -e "$PERL_CLEAN" "$1"; then
    CLEANED=$((CLEANED+1))
    echo "    -> cleaned. Backup: $1.worm-infected.bak  ($(wc -c <"$1" | tr -d ' ') bytes now)"
  else
    echo "    -> could not auto-clean, edit manually (backup kept)"
  fi
}

echo "${BLD}== worm-guard on $(hostname) — scanning: ${DIRS[*]} ==${RST}"
PRUNE=( \( -path '*/node_modules' -o -path '*/.git' -o -path '*/.next' -o -path '*/dist' -o -path '*/build' -o -path '*/AppData/Local/Google/Chrome' -o -path '*/AppData/Local/Microsoft/Edge' \) -prune -o )

# ---------------------------------------------------------------- SYSTEM ---
echo "${BLD}-- S1. Live malicious node processes --${RST}"
PIDS=""
# Check if Windows/MSYS
if [[ "$OSTYPE" == "msys" || "$OSTYPE" == "cygwin" ]]; then
  # Use PowerShell to grab Windows processes since standard ps in Git Bash misses native Windows processes
  PS_PROC=$(powershell.exe -NoProfile -Command "Get-CimInstance Win32_Process -Filter \"name = 'node.exe'\" | Select-Object ProcessId, CommandLine | ConvertTo-Json" 2>/dev/null)
  if [ -n "$PS_PROC" ] && [ "$PS_PROC" != "null" ]; then
    # Parse JSON using perl since jq might not be installed
    PIDS=$(echo "$PS_PROC" | perl -0777 -ne '
      while (/"ProcessId":\s*(\d+).*?"CommandLine":\s*"(.*?)"/gs) {
        my ($pid, $cmd) = ($1, $2);
        if ($cmd =~ /node\s+-e/ || $cmd =~ /global\[\x27_V\x27\]/ || $cmd =~ /global\[\x27_t_t\x27\]/ || $cmd =~ /global\[\x27!\x27\]/ || $cmd =~ /_\$_[0-9a-f]{4}/) {
          print "$pid\n";
        }
      }
    ')
  fi
else
  PIDS=$(ps axww 2>/dev/null | grep -E "node -e|global\['_V'\]|global\['_t_t'\]|global\['!'\]|_\\\$_[0-9a-f]{4}" | grep -vE "grep|worm-guard" | awk '{print $1}')
fi

if [ -n "$PIDS" ]; then
  flag "suspicious node processes running (PIDs: $(echo $PIDS | tr '\n' ' '))"
  if [[ "$OSTYPE" == "msys" || "$OSTYPE" == "cygwin" ]]; then
    powershell.exe -NoProfile -Command "Get-CimInstance Win32_Process -Filter \"name = 'node.exe'\" | Select-Object ProcessId, CommandLine | Format-List" 2>/dev/null | grep -E "ProcessId|CommandLine" | grep -E -B1 -A1 "global|_\$_"
  else
    ps axww | grep -E "node -e" | grep -v grep | cut -c1-160
  fi
  if ask "Kill these processes now?"; then
    for p in $PIDS; do
      if [[ "$OSTYPE" == "msys" || "$OSTYPE" == "cygwin" ]]; then
        taskkill //F //PID "$p" >/dev/null 2>&1 && echo "    -> killed $p" && CLEANED=$((CLEANED+1))
      else
        kill -9 "$p" 2>/dev/null && echo "    -> killed $p" && CLEANED=$((CLEANED+1))
      fi
    done
  fi
else ok "no malicious node processes"; fi

echo "${BLD}-- S2. Active C2 network connection (166.88.54.158) --${RST}"
NET=$(lsof -nP -i 2>/dev/null | grep '166.88.54.158')
[ -n "$NET" ] && flag "ACTIVE C2 connection:"$'\n'"$NET" || ok "no C2 connection"

echo "${BLD}-- S3. Trojanized global npm cli.js --${RST}"
NPMHIT=0
for base in "$HOME/.nvm/versions/node" "$HOME/.fnm" "$HOME/.volta" "$HOME/.local" "$HOME/AppData/Local/nvm" "$HOME/AppData/Roaming/nvm" "/c/nvm4w" "/c/Program Files/nodejs" /usr/local/lib /opt/homebrew/lib /usr/lib; do
  [ -d "$base" ] || continue
  while IFS= read -r c; do
    sz=$(wc -c <"$c" 2>/dev/null | tr -d ' ')
    if [ "${sz:-0}" -gt 10000 ] || grep -qE "_\\\$_[0-9a-f]{4}|global\['!'\]=" "$c" 2>/dev/null; then
      flag "trojanized npm cli.js: $c (${sz} bytes; clean is well under 10KB)"; NPMHIT=1
      if ask "Quarantine this cli.js? (npm for that node version breaks until you reinstall node — that is the point)"; then
        quarantine "$c"
        echo "    -> reinstall that node version afterwards (nvm: 'nvm uninstall <ver> && nvm install <ver>', brew: 'brew reinstall node')"
      fi
    fi
  done < <(find "$base" -path '*/npm/lib/cli.js' 2>/dev/null)
done
[ "$NPMHIT" = 0 ] && ok "global npm cli.js clean (where found)"

echo "${BLD}-- S4. Worm C2 library store ~/.node_modules --${RST}"
if [ -d "$HOME/.node_modules" ] && grep -qsE 'socket\.io-client|axios' "$HOME/.node_modules/package.json" 2>/dev/null; then
  flag "~/.node_modules C2 store exists"
  ask "Quarantine ~/.node_modules?" && quarantine "$HOME/.node_modules"
else ok "no ~/.node_modules C2 store"; fi

# ------------------------------------------------------------ REPO / DIRS --
CFGFIND(){ find "$1" "${PRUNE[@]}" -type f \( -name '*.config.js' -o -name '*.config.mjs' -o -name '*.config.cjs' -o -name '*.config.ts' \) -print 2>/dev/null; }

echo "${BLD}-- 1. Worm payload in config files (all variant markers) --${RST}"
P=0
for d in "${DIRS[@]}"; do
  printf '   scanning %s ...\n' "$d"
  while IFS= read -r f; do
    if grep -qE "global\['!'\]=|global\['_V'\]=|global\['_t_t'\]|_\\\$_[0-9a-f]{4}" "$f" 2>/dev/null; then
      sz=$(wc -c <"$f" | tr -d ' ')
      flag "payload in: $f (${sz} bytes)"
      P=1
      ask "Clean this file? (payload removed, backup kept)" && clean_config "$f"
    elif grep -qE ' {100,}\S' "$f" 2>/dev/null; then
      flag "hidden content after long whitespace run (scroll right!): $f"
      P=1
      ask "Clean this file? (backup kept)" && clean_config "$f"
    elif grep -qE 'trongrid\.io|bsc-dataseed|bsc-rpc\.publicnode' "$f" 2>/dev/null; then
      flag "blockchain C2 endpoint referenced in config: $f"
      P=1
      warn "    -> review manually; if it is a postcss/next/vite config, this is the worm. Backup+clean by hand."
    fi
  done < <(CFGFIND "$d")
done
[ "$P" = 0 ] && ok "no payload in configs"

echo "${BLD}-- 2. Suspiciously large postcss/next configs (normal ~80-200 bytes) --${RST}"
P=0
for d in "${DIRS[@]}"; do
  while IFS= read -r f; do
    sz=$(wc -c <"$f" | tr -d ' ')
    if [ "$sz" -gt 3000 ]; then warn "oversized: $f (${sz} bytes) — open it and scroll right"; P=1; fi
  done < <(find "$d" "${PRUNE[@]}" -type f \( -name 'postcss.config.*' -o -name 'next.config.*' \) ! -name '*.bak' -print 2>/dev/null)
done
[ "$P" = 0 ] && ok "no oversized postcss/next configs"

echo "${BLD}-- 3. Injected createRequire headers --${RST}"
P=0
for d in "${DIRS[@]}"; do
  while IFS= read -r f; do
    if head -5 "$f" 2>/dev/null | grep -q 'createRequire(import.meta.url)'; then
      warn "createRequire header: $f — legit in some projects; if you didn't add it, clean the file"; P=1
    fi
  done < <(CFGFIND "$d")
done
[ "$P" = 0 ] && ok "no createRequire headers"

echo "${BLD}-- 4. Fake font files (.woff2/.ttf that are really JS) --${RST}"
P=0
for d in "${DIRS[@]}"; do
  printf '   scanning %s ...\n' "$d"
  while IFS= read -r ff; do
    hex=$(head -c4 "$ff" 2>/dev/null | od -An -tx1 2>/dev/null | tr -d ' \n')
    case "$hex" in 774f4632|774f4646|4f54544f|74727565|00010000) ;; *)
      flag "fake font: $ff"; P=1
      ask "Quarantine this file?" && quarantine "$ff";;
    esac
  done < <(find "$d" "${PRUNE[@]}" -type f \( -name '*.woff2' -o -name '*.ttf' \) -print 2>/dev/null)
done
[ "$P" = 0 ] && ok "all fonts have valid magic bytes"

echo "${BLD}-- 5. VS Code folderOpen droppers / auto-task enablers --${RST}"
P=0
for d in "${DIRS[@]}"; do
  while IFS= read -r tj; do
    if grep -qi 'folderOpen' "$tj" 2>/dev/null; then
      flag "folderOpen auto-run task: $tj"; P=1
      ask "Quarantine this tasks.json?" && quarantine "$tj"
    fi
  done < <(find "$d" "${PRUNE[@]}" -name 'tasks.json' -print 2>/dev/null)
  while IFS= read -r sj; do
    if grep -q 'task.allowAutomaticTasks' "$sj" 2>/dev/null; then
      flag "allowAutomaticTasks enabled in: $sj"; P=1
      if ask "Remove that setting line? (backup kept)"; then
        cp "$sj" "$sj.worm-infected.bak"
        perl -0777 -i -pe 's/"task\.allowAutomaticTasks"\s*:\s*("[^"]*"|true|false)\s*,?//g; s/,(\s*})/$1/g' "$sj" && CLEANED=$((CLEANED+1)) && echo "    -> removed (backup: $sj.worm-infected.bak)"
      fi
    fi
  done < <(find "$d" "${PRUNE[@]}" -name 'settings.json' -print 2>/dev/null)
done
# also the real VS Code / Cursor user settings:
for sj in "$HOME/Library/Application Support/Code/User/settings.json" "$HOME/Library/Application Support/Cursor/User/settings.json"; do
  if [ -f "$sj" ] && grep -q 'task.allowAutomaticTasks' "$sj" 2>/dev/null; then
    flag "allowAutomaticTasks in editor settings: $sj"; P=1
    if ask "Remove that setting line? (backup kept)"; then
      cp "$sj" "$sj.worm-infected.bak"
      perl -0777 -i -pe 's/"task\.allowAutomaticTasks"\s*:\s*("[^"]*"|true|false)\s*,?//g; s/,(\s*})/$1/g' "$sj" && CLEANED=$((CLEANED+1)) && echo "    -> removed"
    fi
  fi
done
[ "$P" = 0 ] && ok "no folderOpen / auto-task hooks"

echo "${BLD}-- 6. Worm artifact files (push-bots / stealer output) --${RST}"
P=0
for d in "${DIRS[@]}" /tmp /var/tmp; do
  while IFS= read -r af; do
    flag "worm artifact: $af"; P=1
    ask "Quarantine this file?" && quarantine "$af"
  done < <(find "$d" "${PRUNE[@]}" -maxdepth 8 -type f \( -name 'temp_auto_push.bat' -o -name 'temp_interactive_push.bat' -o -name 'branch_structure.json' -o -name 'truffleSecrets.json' -o -name 'search_tokens.sh' \) -print 2>/dev/null)
done
[ "$P" = 0 ] && ok "no worm artifacts"

# ----------------------------------------------------------------- VERDICT -
echo
if [ "$HITS" -eq 0 ]; then
  echo "${GRN}${BLD}==> CLEAN: no worm indicators found in scanned locations.${RST}"
  exit 0
fi
echo "${RED}${BLD}==> $HITS indicator(s) found. $CLEANED item(s) cleaned/quarantined.${RST}"
[ -d "$QDIR" ] && echo "Quarantine folder: $QDIR (review, then delete it when confident)"
cat <<'EOF'

NEXT STEPS (the malware steals credentials — cleaning files is not enough):
 1. Reboot (kills any in-memory payload; this strain has no boot persistence).
 2. Re-run this script until it reports CLEAN.
 3. Rotate credentials it could have read:
    - GitHub personal access tokens + check Settings > Sessions for strangers
    - npm tokens, every secret in any .env file on this machine
    - clear cached git credentials (macOS: Keychain Access > search "github")
 4. Audit your GitHub repos in the WEB interface: the worm pushes infected
    commits with your cached creds. Check postcss/next configs on GitHub and
    recent commits you don't recognize, on EVERY branch.
 5. If you hold crypto wallets on this machine, move funds to a fresh wallet.
 6. Tell your team which repo/branch was infected so they can clean upstream.
EOF
exit 1
