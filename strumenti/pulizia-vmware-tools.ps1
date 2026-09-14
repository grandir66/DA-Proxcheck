<#
.SYNOPSIS
  Toglie i resti dei VMware Tools da una VM Windows gia' migrata su Proxmox VE.

.DESCRIPTION
  E' il passo C di §11.9.4 del manuale operativo Proxmox VE di Domarc: si usa
  SOLO quando la disinstallazione normale e l'installer con /c hanno fallito.
  Senza parametri fa l'ELENCO di cio' che trova (servizi, driver, pacchetti
  driver, dispositivi fantasma, cartelle, chiavi di registro) e non tocca
  niente. Con -Applica agisce, nell'ordine: ferma e cancella i servizi, toglie
  i driver dal registro, i pacchetti dal driver store, i dispositivi fantasma,
  le cartelle, le chiavi. Poi bisogna riavviare.

  Da eseguire in un PowerShell AMMINISTRATORE, con uno snapshot Proxmox fatto
  prima, e collaudato su una VM di prova: la lista e' quella nota dalle guide
  e dai casi visti, non una garanzia per ogni versione dei Tools.

.PARAMETER Applica
  Esegue le rimozioni. Senza, solo l'elenco.

.EXAMPLE
  .\pulizia-vmware-tools.ps1            # cosa c'e'
  .\pulizia-vmware-tools.ps1 -Applica   # toglie, poi Restart-Computer

.NOTES
  Domarc Srl — DA-Proxmox/strumenti. Fonti: KB Broadcom 315639; guide
  matrixpost.net e vladan.fr (2026-09-15). Versione 1, non ancora collaudata
  su ogni versione dei Tools: leggere l'elenco prima di -Applica.
#>
[CmdletBinding()]
param([switch]$Applica)

$ErrorActionPreference = "Continue"
if (-not ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    Write-Host "Serve un PowerShell amministratore." -ForegroundColor Red; exit 1
}
$modo = if ($Applica) { "APPLICO" } else { "solo elenco (aggiungere -Applica per agire)" }
Write-Host "== Pulizia VMware Tools — $modo ==" -ForegroundColor Cyan

# 1. servizi: quelli dei Tools e dei loro componenti
$serviziNoti = @("VMTools", "VGAuthService", "vm3dservice", "VMwareCAFManagementAgentHost", "VMwareCAFCommAmqpListener",
                 "vmvss", "VMUSBArbService", "vmware-view-usbd", "GISvc")
$servizi = Get-Service -ErrorAction SilentlyContinue | Where-Object {
    $serviziNoti -contains $_.Name -or $_.DisplayName -like "VMware*" -or $_.DisplayName -like "GISvc*" }
Write-Host "`n-- Servizi ($($servizi.Count))"
foreach ($s in $servizi) {
    Write-Host ("  {0,-34} {1,-10} {2}" -f $s.Name, $s.Status, $s.DisplayName)
    if ($Applica) {
        Stop-Service -Name $s.Name -Force -ErrorAction SilentlyContinue
        & sc.exe config $s.Name start= disabled | Out-Null
        & sc.exe delete $s.Name | Out-Null
    }
}

# 2. driver in kernel: voci in CurrentControlSet\Services e i loro .sys
$driverNoti = @("vmci", "vsock", "vm3dmp", "vm3dmp-debug", "vm3dmp-stats", "vm3dmp_loader", "vmaudio", "vmhgfs", "vmmemctl",
                "vmmouse", "vmrawdsk", "vmusbmouse", "vmvss", "vmxnet3", "vmxnet3ndis6", "pvscsi", "VMwareCAF", "vmgencounter")
$chiaviServizi = "HKLM:\SYSTEM\CurrentControlSet\Services"
$driver = Get-ChildItem $chiaviServizi -ErrorAction SilentlyContinue | Where-Object {
    $driverNoti -contains $_.PSChildName -or $_.PSChildName -like "VMware*" }
Write-Host "`n-- Driver nel registro ($($driver.Count))"
foreach ($d in $driver) {
    $img = (Get-ItemProperty $d.PSPath -ErrorAction SilentlyContinue).ImagePath
    Write-Host ("  {0,-24} {1}" -f $d.PSChildName, $img)
    if ($Applica) {
        Remove-Item -Path $d.PSPath -Recurse -Force -ErrorAction SilentlyContinue
        if ($img) {
            $sys = $img -replace '^\\SystemRoot\\', "$env:SystemRoot\" -replace '^System32\\', "$env:SystemRoot\System32\" -replace '^\\\?\?\\', ''
            if (Test-Path $sys) { Remove-Item $sys -Force -ErrorAction SilentlyContinue }
        }
    }
}

# 3. pacchetti driver nel driver store (i .inf di VMware)
$inf = & pnputil /enum-drivers 2>$null
$pacchetti = @(); $corrente = $null
foreach ($riga in $inf) {
    if ($riga -match '^(Published Name|Nome pubblicato)\s*:\s*(oem\d+\.inf)') { $corrente = $matches[2] }
    if ($riga -match '^(Driver package provider|Provider del pacchetto driver|Provider)\s*:\s*(.*VMware.*)' -and $corrente) { $pacchetti += $corrente; $corrente = $null }
}
Write-Host "`n-- Pacchetti driver VMware nel driver store ($($pacchetti.Count))"
foreach ($p in $pacchetti) {
    Write-Host "  $p"
    if ($Applica) { & pnputil /delete-driver $p /uninstall /force | Out-Null }
}

# 4. dispositivi fantasma (NIC vmxnet3 con l'IP vecchio, controller PVSCSI, VMCI...)
$fantasmi = Get-PnpDevice -PresentOnly:$false -ErrorAction SilentlyContinue | Where-Object {
    ($_.FriendlyName -like "*VMware*" -or $_.FriendlyName -like "*vmxnet*" -or $_.FriendlyName -like "*PVSCSI*") -and $_.Status -ne "OK" }
Write-Host "`n-- Dispositivi VMware non presenti ($($fantasmi.Count))"
foreach ($f in $fantasmi) {
    Write-Host ("  {0,-50} {1}" -f $f.FriendlyName, $f.InstanceId)
    if ($Applica) { & pnputil /remove-device "$($f.InstanceId)" | Out-Null }
}

# 5. cartelle
$cartelle = @("$env:ProgramFiles\VMware", "${env:ProgramFiles(x86)}\VMware", "$env:ProgramFiles\Common Files\VMware",
              "$env:ProgramData\VMware") | Where-Object { $_ -and (Test-Path $_) }
Write-Host "`n-- Cartelle ($($cartelle.Count))"
foreach ($c in $cartelle) {
    Write-Host "  $c"
    if ($Applica) { Remove-Item -Path $c -Recurse -Force -ErrorAction SilentlyContinue }
}

# 6. chiavi di registro: il prodotto e la voce di disinstallazione (per ultima)
$chiavi = @("HKLM:\SOFTWARE\VMware, Inc.", "HKLM:\SOFTWARE\WOW6432Node\VMware, Inc.") | Where-Object { Test-Path $_ }
$disinst = @()
foreach ($radice in @("HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall", "HKLM:\SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall")) {
    $disinst += Get-ChildItem $radice -ErrorAction SilentlyContinue | Where-Object {
        (Get-ItemProperty $_.PSPath -ErrorAction SilentlyContinue).DisplayName -like "VMware Tools*" }
}
Write-Host "`n-- Chiavi di registro ($($chiavi.Count + $disinst.Count))"
foreach ($k in $chiavi) { Write-Host "  $k"; if ($Applica) { Remove-Item -Path $k -Recurse -Force -ErrorAction SilentlyContinue } }
foreach ($k in $disinst) { Write-Host "  $($k.PSPath -replace '^Microsoft\.PowerShell\.Core\\Registry::', '') (voce di disinstallazione)"
    if ($Applica) { Remove-Item -Path $k.PSPath -Recurse -Force -ErrorAction SilentlyContinue } }

if ($Applica) {
    Write-Host "`nFatto. Riavviare, poi verificare: Get-Service VM* vuoto, nessun dispositivo VMware nascosto, qm agent ping dal nodo, un backup che congela il guest." -ForegroundColor Green
} else {
    Write-Host "`nNiente e' stato toccato. Con uno snapshot fatto: .\pulizia-vmware-tools.ps1 -Applica" -ForegroundColor Yellow
}
