; Compile only through scripts/build_installer.ps1 (version and provenance gates).
#ifndef AppVersion
  #error AppVersion is required
#endif
#ifndef ClientExe
  #error ClientExe is required
#endif
#ifndef BuildOutput
  #error BuildOutput is required
#endif
#ifndef ArtifactName
  #error ArtifactName is required
#endif

[Setup]
AppId={{B2A7D908-2FA3-45B4-AC85-36035DDBE4DD}
AppName=TracerTransfer Cliente
AppVersion={#AppVersion}
AppPublisher=TracerTransfer
AppVerName=TracerTransfer Cliente {#AppVersion}
VersionInfoVersion={#AppVersion}
DefaultDirName={localappdata}\Programs\TracerTransfer\Client
DisableDirPage=yes
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
OutputDir={#BuildOutput}
OutputBaseFilename={#ArtifactName}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
DisableWelcomePage=no
UninstallDisplayName=TracerTransfer Cliente
UninstallDisplayIcon={app}\TracerCliente.exe
SetupLogging=yes
CloseApplications=no
RestartApplications=no
UsePreviousTasks=no
#ifdef SignToolName
SignTool={#SignToolName}
SignedUninstaller=yes
#else
SignedUninstaller=no
#endif

[Languages]
Name: "brazilianportuguese"; MessagesFile: "compiler:Languages\BrazilianPortuguese.isl"

[Messages]
#ifdef PilotUnsigned
WelcomeLabel2=Este é um piloto NÃO ASSINADO do TracerTransfer Cliente {#AppVersion}. Não desative proteções do Windows para executá-lo.%n%nA instalação é somente para este usuário. Configuração, ativação e fila existentes serão preservadas. Encerre o cliente pela bandeja antes de atualizar ou desinstalar.
#endif

[Tasks]
Name: "desktopicon"; Description: "Criar atalho na área de trabalho"; Flags: unchecked
Name: "startup"; Description: "Iniciar na bandeja ao entrar no Windows (opcional)"; Flags: unchecked

[Files]
Source: "{#ClientExe}"; DestDir: "{app}"; DestName: "TracerCliente.exe"; Flags: ignoreversion
#ifdef ClientProfile
Source: "{#ClientProfile}"; DestDir: "{app}"; DestName: "deployment.json"; Flags: onlyifdoesntexist uninsneveruninstall
#endif

[Icons]
Name: "{userprograms}\TracerTransfer\TracerTransfer Cliente"; Filename: "{app}\TracerCliente.exe"; WorkingDir: "{app}"
Name: "{userprograms}\TracerTransfer\Desinstalar TracerTransfer Cliente"; Filename: "{uninstallexe}"
Name: "{userdesktop}\TracerTransfer Cliente"; Filename: "{app}\TracerCliente.exe"; WorkingDir: "{app}"; Tasks: desktopicon

[Registry]
; Exactly the same HKCU Run value used by client.config_ui.set_startup.
; An unchecked task leaves an existing preference untouched on update.
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: string; ValueName: "{code:StartupValueName}"; ValueData: """{app}\TracerCliente.exe"" --background --data-dir ""{localappdata}\TracerTransfer\client"""; Tasks: startup; Flags: uninsdeletevalue

[Run]
Filename: "{app}\TracerCliente.exe"; Description: "Abrir TracerTransfer Cliente para ativar e escolher a pasta"; Flags: nowait postinstall skipifsilent unchecked

[Code]
function StartupValueName(Param: String): String;
begin
  Result := 'TracerTransfer-' + Copy(GetSHA256OfString(
    UTF8Encode(Lowercase(ExpandConstant('{localappdata}\TracerTransfer\client')))), 1, 16);
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
begin
  if CurUninstallStep = usUninstall then
    { Also remove an autostart created later by the client UI, not only by Setup. }
    RegDeleteValue(HKCU, 'Software\Microsoft\Windows\CurrentVersion\Run', StartupValueName(''));
  { Deliberately no deletion of client data, config, activation, queue or source files. }
end;
