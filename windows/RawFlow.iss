; Instalador de RawFlow para Windows (Inno Setup 6). Lo compila «build_windows.bat»:
;   ISCC /DAppVer=4.18 windows\RawFlow.iss   →   dist\windows\RawFlow 4.18 para Windows (Instalador).exe
; Se instala solo para el usuario (sin permisos de administrador) y no necesita Python ni internet.

#ifndef AppVer
  #define AppVer "0.0"
#endif

[Setup]
AppId={{8E0B6D0C-6C1B-4E7B-9B55-5A1F0C7A4D18}
AppName=RawFlow
AppVersion={#AppVer}
AppVerName=RawFlow {#AppVer}
AppPublisher=RawFlow
VersionInfoVersion={#AppVer}.0.0
VersionInfoDescription=Instalador de RawFlow
DefaultDirName={localappdata}\Programs\RawFlow
DisableDirPage=yes
DisableProgramGroupPage=yes
DisableReadyPage=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
SetupIconFile=..\rawflow\RawFlow.ico
UninstallDisplayIcon={app}\RawFlow.exe
UninstallDisplayName=RawFlow
WizardStyle=modern
WizardImageFile=..\build\windows\wizard.bmp
WizardSmallImageFile=..\build\windows\wizard_s.bmp
ShowLanguageDialog=no
Compression=lzma2/max
SolidCompression=yes
CloseApplications=yes
RestartApplications=no
OutputDir=..\dist\windows
OutputBaseFilename=RawFlow {#AppVer} para Windows (Instalador)

[Languages]
Name: "es"; MessagesFile: "compiler:Languages\Spanish.isl"

[Tasks]
Name: "desktopicon"; Description: "Crear un icono en el Escritorio"

[InstallDelete]
; Restos de la instalación antigua (Python + .venv) y de la versión anterior: se instala siempre en limpio.
Type: filesandordirs; Name: "{app}\.venv"
Type: filesandordirs; Name: "{app}\rawflow"
Type: filesandordirs; Name: "{app}\tests"
Type: filesandordirs; Name: "{app}\tools"
Type: filesandordirs; Name: "{app}\_internal"
Type: files; Name: "{app}\RawFlow.py"
Type: files; Name: "{app}\requirements.txt"
Type: files; Name: "{app}\README.md"

[Files]
Source: "..\dist\windows\RawFlow\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\RawFlow"; Filename: "{app}\RawFlow.exe"; WorkingDir: "{app}"; Comment: "RawFlow"
Name: "{autodesktop}\RawFlow"; Filename: "{app}\RawFlow.exe"; WorkingDir: "{app}"; Comment: "RawFlow"; Tasks: desktopicon

[Run]
Filename: "{app}\RawFlow.exe"; Description: "Abrir RawFlow"; Flags: nowait postinstall skipifsilent
; Actualización desde la propia RawFlow (instalador en silencio): al terminar se vuelve a abrir sola.
Filename: "{app}\RawFlow.exe"; Flags: nowait; Check: WizardSilent

[UninstallDelete]
; Solo el programa: las fotos, los eventos y los ajustes (AppData\Roaming\RawFlow) no se tocan.
Type: filesandordirs; Name: "{app}"

[Code]
// Cierra RawFlow si está abierto (el .exe y la versión antigua, que corría con pythonw) antes de copiar o de quitar.
procedure CerrarRawFlow();
var
  R: Integer;
begin
  Exec(ExpandConstant('{sys}\WindowsPowerShell\v1.0\powershell.exe'),
    '-NoProfile -ExecutionPolicy Bypass -Command "Get-CimInstance Win32_Process | Where-Object { ' +
    '$_.Name -eq ''RawFlow.exe'' -or ($_.Name -like ''python*.exe'' -and $_.CommandLine -like ''*RawFlow.py*'') } | ' +
    'ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }"',
    '', SW_HIDE, ewWaitUntilTerminated, R);
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
begin
  CerrarRawFlow();
  Result := '';
end;

function InitializeUninstall(): Boolean;
begin
  CerrarRawFlow();
  Result := True;
end;
