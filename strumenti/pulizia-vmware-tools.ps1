<#
.SYNOPSIS
  Toglie i resti dei VMware Tools da una VM Windows gia' migrata su Proxmox VE.

.DESCRIPTION
  E' il passo C di §11.9.4 del manuale operativo Proxmox VE di Domarc: si usa
  SOLO quando la disinstallazione normale e l'installer con /c hanno fallito.
  Senza parametri fa l'ELENCO di cio' che trova e non tocca niente. Con
  -Applica agisce, nell'ordine: ferma e cancella i servizi, toglie i driver dal
  registro e dal driver store, i dispositivi fantasma, le cartelle, le chiavi.
  Poi bisogna riavviare.

  Tocca SOLO i componenti dei VMware Tools, per nome esatto: non «tutto cio'
  che si chiama VMware». Se trova altri prodotti VMware installati (Horizon
  Agent, Carbon Black, Workspace ONE, App Volumes...) li elenca e con -Applica
  si ferma, a meno di -Forza: quelli hanno i loro disinstallatori.

  Da eseguire in un PowerShell AMMINISTRATORE, con uno snapshot Proxmox fatto
  prima, e collaudato su una VM di prova: la lista e' quella nota dalle guide e
  dai casi visti, non una garanzia per ogni versione dei Tools. Alla fine dice
  cosa non e' riuscito a togliere, e in quel caso esce con codice 1.

.PARAMETER Applica
  Esegue le rimozioni. Senza, solo l'elenco.

.PARAMETER Forza
  Con -Applica: procede anche se ci sono altri prodotti VMware installati
  (che NON vengono toccati, ma potrebbero dipendere da componenti condivisi).

.EXAMPLE
  .\pulizia-vmware-tools.ps1            # cosa c'e'
  .\pulizia-vmware-tools.ps1 -Applica   # toglie, poi Restart-Computer

.NOTES
  Domarc Srl — DA-Proxmox/strumenti. Fonti: KB Broadcom 315639; guide
  matrixpost.net e vladan.fr (2026-09-15). Versione 2 (revisione del 15/09:
  perimetro ristretto ai Tools, controllo degli altri prodotti, esito onesto).
  Non ancora collaudata su ogni versione dei Tools: leggere l'elenco prima.
#>
[CmdletBinding()]
param([switch]$Applica, [switch]$Forza)

$ErrorActionPreference = "Continue"
if (-not ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    Write-Host "Serve un PowerShell amministratore." -ForegroundColor Red; exit 1
}
$modo = if ($Applica) { "APPLICO" } else { "solo elenco (aggiungere -Applica per agire)" }
Write-Host "== Pulizia VMware Tools — $modo ==" -ForegroundColor Cyan
$falliti = New-Object System.Collections.Generic.List[string]
function Prova($cosa, [scriptblock]$azione) {
    try { & $azione } catch { $falliti.Add("$cosa — $($_.Exception.Message)") }
}

# 0. altri prodotti VMware: si elencano, non si toccano, e con -Applica fermano
$radiciDisinst = @("HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall",
                   "HKLM:\SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall")
$vociVMware = foreach ($r in $radiciDisinst) {
    Get-ChildItem $r -ErrorAction SilentlyContinue | ForEach-Object { Get-ItemProperty $_.PSPath -ErrorAction SilentlyContinue } |
        Where-Object { $_.DisplayName -like "VMware*" } }
$vociTools = @($vociVMware | Where-Object { $_.DisplayName -eq "VMware Tools" })
$altriProdotti = @($vociVMware | Where-Object { $_.DisplayName -ne "VMware Tools" })
Write-Host "`n-- Prodotti VMware installati: Tools $(if ($vociTools) { 'presente (' + $vociTools[0].DisplayVersion + ')' } else { 'non registrato' })"
foreach ($a in $altriProdotti) { Write-Host ("  ALTRO: {0} {1}  — non viene toccato" -f $a.DisplayName, $a.DisplayVersion) -ForegroundColor Yellow }
if ($Applica -and $altriProdotti.Count -gt 0 -and -not $Forza) {
    Write-Host "`nCi sono altri prodotti VMware: questo script toglie solo i Tools, ma alcuni componenti (VMCI, VGAuth) potrebbero servire anche a loro. Disinstallarli prima con il loro programma, oppure rilanciare con -Forza." -ForegroundColor Red
    exit 1
}

# 1. servizi dei Tools, per nome esatto
$serviziNoti = @("VMTools", "VGAuthService", "vm3dservice", "VMwareCAFManagementAgentHost", "VMwareCAFCommAmqpListener",
                 "vmvss", "VMUSBArbService", "GISvc")
$servizi = @(Get-Service -ErrorAction SilentlyContinue | Where-Object { $serviziNoti -contains $_.Name })
Write-Host "`n-- Servizi ($($servizi.Count))"
foreach ($s in $servizi) {
    Write-Host ("  {0,-34} {1,-10} {2}" -f $s.Name, $s.Status, $s.DisplayName)
    if ($Applica) {
        Prova "servizio $($s.Name)" {
            Stop-Service -Name $s.Name -Force -ErrorAction SilentlyContinue
            $r = & sc.exe delete $s.Name 2>&1; if ($LASTEXITCODE -ne 0) { throw "sc delete: $r" }
        }
    }
}

# 2. driver in kernel dei Tools, per nome esatto
$driverNoti = @("vmci", "vsock", "vm3dmp", "vm3dmp-debug", "vm3dmp-stats", "vm3dmp_loader", "vmaudio", "vmhgfs", "vmmemctl",
                "vmmouse", "vmrawdsk", "vmusbmouse", "vmvss", "vmxnet3", "vmxnet3ndis6", "pvscsi", "vmgencounter",
                "VMwareCAFCommAmqpListener", "VMwareCAFManagementAgentHost")
$chiaviServizi = "HKLM:\SYSTEM\CurrentControlSet\Services"
$driver = @(Get-ChildItem $chiaviServizi -ErrorAction SilentlyContinue | Where-Object { $driverNoti -contains $_.PSChildName })
Write-Host "`n-- Driver nel registro ($($driver.Count))"
foreach ($d in $driver) {
    $img = (Get-ItemProperty $d.PSPath -ErrorAction SilentlyContinue).ImagePath
    Write-Host ("  {0,-28} {1}" -f $d.PSChildName, $img)
    if ($Applica) {
        Prova "driver $($d.PSChildName)" {
            Remove-Item -Path $d.PSPath -Recurse -Force -ErrorAction Stop
            if ($img) {
                $sys = $img -replace '^\\SystemRoot\\', "$env:SystemRoot\" -replace '^System32\\', "$env:SystemRoot\System32\" -replace '^\\\?\?\\', ''
                if (Test-Path $sys) { Remove-Item $sys -Force -ErrorAction Stop }
            }
        }
    }
}

# 3. pacchetti driver nel driver store: solo quelli il cui provider e' VMware E la classe e' dei Tools
$inf = & pnputil /enum-drivers 2>$null
$pacchetti = @(); $corrente = $null; $provider = $null; $originale = $null
$infTools = @("vmxnet3.inf", "pvscsi.inf", "vmci.inf", "vsock.inf", "vmmouse.inf", "vmusbmouse.inf", "vm3d.inf", "vmaudio.inf",
              "vmhgfs.inf", "vmmemctl.inf", "vmrawdsk.inf", "vmxnet3ndis6.inf", "efifw.inf", "vmgencounter.inf", "vmvss.inf")
foreach ($riga in $inf) {
    if ($riga -match '^(Published Name|Nome pubblicato)\s*:\s*(oem\d+\.inf)') { $corrente = $matches[2]; $provider = $null; $originale = $null }
    if ($riga -match '^(Original Name|Nome originale)\s*:\s*(\S+)') { $originale = $matches[2].ToLower() }
    if ($riga -match '^(Driver package provider|Provider del pacchetto driver|Provider)\s*:\s*(.*)') { $provider = $matches[2] }
    if ($corrente -and $provider -and $originale -and $provider -like "*VMware*" -and $infTools -contains $originale) {
        $pacchetti += [pscustomobject]@{ Pubblicato = $corrente; Originale = $originale }; $corrente = $null
    }
}
Write-Host "`n-- Pacchetti driver dei Tools nel driver store ($($pacchetti.Count))"
foreach ($p in $pacchetti) {
    Write-Host ("  {0,-12} {1}" -f $p.Pubblicato, $p.Originale)
    if ($Applica) { Prova "pacchetto $($p.Originale)" { $r = & pnputil /delete-driver $p.Pubblicato /uninstall /force 2>&1; if ($LASTEXITCODE -ne 0) { throw "pnputil: $r" } } }
}

# 4. dispositivi fantasma dei Tools (non presenti): NIC vmxnet3, controller PVSCSI, VMCI, SVGA, mouse/USB VMware
$nomiDispositivi = @("*vmxnet*", "*PVSCSI*", "*VMware VMCI*", "*VMware SVGA*", "*VMware Pointing*", "*VMware USB*", "*VMware Virtual S*")
$fantasmi = @(Get-PnpDevice -PresentOnly:$false -ErrorAction SilentlyContinue | Where-Object {
    $n = $_.FriendlyName; $_.Status -ne "OK" -and $n -notlike "*Horizon*" -and ($nomiDispositivi | Where-Object { $n -like $_ }).Count -gt 0 })
Write-Host "`n-- Dispositivi dei Tools non presenti ($($fantasmi.Count))"
foreach ($f in $fantasmi) {
    Write-Host ("  {0,-50} {1}" -f $f.FriendlyName, $f.InstanceId)
    if ($Applica) { Prova "dispositivo $($f.FriendlyName)" { $r = & pnputil /remove-device "$($f.InstanceId)" 2>&1; if ($LASTEXITCODE -ne 0) { throw "pnputil: $r" } } }
}

# 5. cartelle dei Tools (non «VMware» intera: li' stanno anche gli altri prodotti)
$cartelle = @("$env:ProgramFiles\VMware\VMware Tools", "${env:ProgramFiles(x86)}\VMware\VMware Tools",
              "$env:ProgramFiles\Common Files\VMware\Drivers", "$env:ProgramData\VMware\VMware Tools",
              "$env:ProgramData\VMware\VMware CAF", "$env:ProgramData\VMware\VMware VGAuth") | Where-Object { $_ -and (Test-Path $_) }
Write-Host "`n-- Cartelle ($($cartelle.Count))"
foreach ($c in $cartelle) {
    Write-Host "  $c"
    if ($Applica) { Prova "cartella $c" { Remove-Item -Path $c -Recurse -Force -ErrorAction Stop } }
}

# 6. chiavi di registro dei Tools, e la voce di disinstallazione «VMware Tools» per ultima
$chiavi = @()
foreach ($radice in @("HKLM:\SOFTWARE\VMware, Inc.", "HKLM:\SOFTWARE\WOW6432Node\VMware, Inc.")) {
    foreach ($sotto in @("VMware Tools", "VMware VGAuth", "VMware CAF", "VMware Drivers")) {
        if (Test-Path "$radice\$sotto") { $chiavi += "$radice\$sotto" } } }
$disinst = @()
foreach ($r in $radiciDisinst) {
    $disinst += Get-ChildItem $r -ErrorAction SilentlyContinue | Where-Object {
        (Get-ItemProperty $_.PSPath -ErrorAction SilentlyContinue).DisplayName -eq "VMware Tools" } }
Write-Host "`n-- Chiavi di registro ($($chiavi.Count + $disinst.Count))"
foreach ($k in $chiavi) { Write-Host "  $k"; if ($Applica) { Prova "chiave $k" { Remove-Item -Path $k -Recurse -Force -ErrorAction Stop } } }
foreach ($k in $disinst) { Write-Host "  $($k.PSPath -replace '^Microsoft\.PowerShell\.Core\\Registry::', '') (voce di disinstallazione)"
    if ($Applica) { Prova "voce di disinstallazione" { Remove-Item -Path $k.PSPath -Recurse -Force -ErrorAction Stop } } }

if (-not $Applica) {
    Write-Host "`nNiente e' stato toccato. Con uno snapshot fatto: .\pulizia-vmware-tools.ps1 -Applica" -ForegroundColor Yellow
    exit 0
}
if ($falliti.Count -gt 0) {
    Write-Host "`nNON tutto e' stato tolto ($($falliti.Count) passi falliti):" -ForegroundColor Red
    foreach ($f in $falliti) { Write-Host "  ✘ $f" -ForegroundColor Red }
    Write-Host "Riavviare e rilanciare: i file in uso si liberano al riavvio. Quello che resta si toglie a mano (manuale §11.9.4)." -ForegroundColor Yellow
    exit 1
}
Write-Host "`nTolto tutto quello che era in elenco. Riavviare, poi verificare: Get-Service VM* vuoto, nessun dispositivo VMware nascosto, qm agent ping dal nodo, un backup che congela il guest." -ForegroundColor Green
exit 0
