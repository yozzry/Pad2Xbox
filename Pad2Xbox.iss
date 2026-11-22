#define MyAppName "Pad2Xbox"
#define MyAppVersion "1.0"
#define MyAppPublisher "Pad2Xbox"
#define MyAppExeName "Pad2Xbox.exe"

[Setup]
AppId={{B8F3C2A1-4D5E-6F7A-8B9C-0D1E2F3A4B5C}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={autopf}\{#MyAppName}
DefaultGroupName={#MyAppName}
AllowNoIcons=yes
OutputDir=C:\Users\Administrator\Desktop
OutputBaseFilename=Pad2Xbox_Setup
Compression=lzma
SolidCompression=yes
WizardStyle=modern
PrivilegesRequired=admin
ArchitecturesInstallIn64BitMode=x64

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"
Name: "autostart"; Description: "Start automatically when I log in to Windows"; GroupDescription: "Startup"; Flags: unchecked

[Files]
Source: "C:\Users\Administrator\Desktop\New folder (4)\dist\Pad2Xbox\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "C:\Temp\opencode\ViGEmBus_installer.exe"; DestDir: "{tmp}"; Flags: deleteafterinstall
Source: "C:\Temp\opencode\HidHide_1.5.230_x64.exe"; DestDir: "{tmp}"; Flags: deleteafterinstall

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon
Name: "{userstartup}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Parameters: "--autostart"; Tasks: autostart

[Run]
Filename: "{tmp}\ViGEmBus_installer.exe"; Parameters: "/VERYSILENT /SUPPRESSMSGBOXES /NORESTART"; StatusMsg: "Installing ViGEmBus driver..."; Flags: waituntilterminated
Filename: "{tmp}\HidHide_1.5.230_x64.exe"; Parameters: "/VERYSILENT /SUPPRESSMSGBOXES /NORESTART"; StatusMsg: "Installing HidHide driver..."; Flags: waituntilterminated
Filename: "{app}\{#MyAppExeName}"; Description: "{cm:LaunchProgram,{#StringChange(MyAppName, '&', '&&')}}"; Flags: nowait postinstall skipifsilent

[UninstallRun]
Filename: "schtasks"; Parameters: "/Delete /TN Pad2Xbox /F"; Flags: runhidden

[Code]
procedure CurStepChanged(CurStep: TSetupStep);
begin
  if CurStep = ssPostInstall then
  begin
    // Register auto-start task if selected
  end;
end;