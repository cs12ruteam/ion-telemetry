; Open this file in Inno Setup Compiler from the Turing 3.10.0 project root.
; Build-IonTuring.ps1 must have produced dist-ion\turing-system-monitor first.
#define Bundle "dist-ion\turing-system-monitor"
#define AppName "Turing Ion Telemetry"

[Setup]
AppId={{18CB6973-55F9-47A4-98F7-D2A53C28578B}
AppName={#AppName}
AppVersion=3.10.0
AppPublisher=Ion Telemetry
AppPublisherURL=https://github.com/mathoudebine/turing-smart-screen-python
DefaultDirName={localappdata}\Programs\{#AppName}
DefaultGroupName={#AppName}
UninstallDisplayIcon={app}\main.exe
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
OutputDir=setup-ion
OutputBaseFilename=Turing-Ion-Telemetry-3.10.0-Setup
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
LicenseFile=LICENSE
SetupIconFile=res\icons\monitor-icon-17865\icon.ico

[Languages]
Name: "russian"; MessagesFile: "compiler:Languages\Russian.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[Files]
Source: "{#Bundle}\*"; Excludes: "\config.yaml"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "{#Bundle}\config.yaml"; DestDir: "{app}"; Flags: onlyifdoesntexist
Source: "LICENSE"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\Turing Ion Telemetry"; Filename: "{app}\main.exe"; WorkingDir: "{app}"
Name: "{group}\Configure Ion Telemetry"; Filename: "{app}\configure.exe"; WorkingDir: "{app}"
Name: "{group}\Uninstall Ion Telemetry"; Filename: "{uninstallexe}"
