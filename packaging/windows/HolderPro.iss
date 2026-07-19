#ifndef SourceDir
  #error SourceDir must point at the PyInstaller HolderPro directory
#endif
#ifndef OutputDir
  #error OutputDir must be provided
#endif
#ifndef AppVersion
  #error AppVersion must be provided
#endif

[Setup]
AppId={{0E3DBFB4-7EC4-49CF-9F95-2E5FA51D673D}
AppName=HolderPro
AppVersion={#AppVersion}
AppPublisher=HolderPro contributors
AppPublisherURL=https://github.com/Finn2400/HolderPro
AppSupportURL=https://github.com/Finn2400/HolderPro/issues
AppUpdatesURL=https://github.com/Finn2400/HolderPro/releases
DefaultDirName={localappdata}\Programs\HolderPro
DefaultGroupName=HolderPro
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir={#OutputDir}
OutputBaseFilename=HolderPro-{#AppVersion}-windows-x86_64-setup
Compression=lzma2/ultra64
SolidCompression=yes
WizardStyle=modern
LicenseFile=..\..\LICENSE
UninstallDisplayIcon={app}\HolderPro.exe

[Files]
Source: "{#SourceDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\HolderPro"; Filename: "{app}\HolderPro.exe"
Name: "{userdesktop}\HolderPro"; Filename: "{app}\HolderPro.exe"; Tasks: desktopicon

[Tasks]
Name: "desktopicon"; Description: "Create a &desktop shortcut"; GroupDescription: "Additional icons:"; Flags: unchecked

[Run]
Filename: "{app}\HolderPro.exe"; Description: "Launch HolderPro"; Flags: nowait postinstall skipifsilent
