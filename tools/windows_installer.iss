; Build with Inno Setup 6.7.3 and the corresponding upstream ChineseSimplified.isl.
; Supply /O<directory> for the delivery folder. Only the product files below are installed.
#define ProductRoot ".."
#define VersionLine FileOpen(AddBackslash(SourcePath)+"..\app\__init__.py")
#expr FileRead(VersionLine)
#define VersionDef FileRead(VersionLine)
#define ProductVersion Copy(VersionDef, Pos('"',VersionDef)+1, Len(Trim(VersionDef))-Pos('"',VersionDef)-1)
#expr FileClose(VersionLine)

[Setup]
AppId={{C80EFA9F-5585-43DA-A439-49D0BC1441C2}
AppName=物理实验助手
AppVersion={#ProductVersion}
AppPublisher=物理实验助手项目
DefaultDirName={localappdata}\Programs\PhysicsExperimentAssistant
DefaultGroupName=物理实验助手
DisableProgramGroupPage=yes
DisableDirPage=no
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
WizardStyle=modern
Compression=lzma2/fast
SolidCompression=yes
OutputBaseFilename=01-物理实验助手-{#ProductVersion}-安装程序
UninstallDisplayIcon={app}\runtime\pythonw.exe
CloseApplications=no

[Languages]
Name: "chinesesimplified"; MessagesFile: "compiler:Languages\ChineseSimplified.isl"

[Files]
Source: "{#ProductRoot}\app\*.py"; DestDir: "{app}\app"; Flags: ignoreversion recursesubdirs
Source: "{#ProductRoot}\app\knowledge_seed.json"; DestDir: "{app}\app"; Flags: ignoreversion
Source: "{#ProductRoot}\formats\*"; DestDir: "{app}\formats"; Flags: ignoreversion recursesubdirs
Source: "{#ProductRoot}\runtime\*"; DestDir: "{app}\runtime"; Excludes: "__pycache__\*,*.pyc"; Flags: ignoreversion recursesubdirs
Source: "{#ProductRoot}\licenses\*"; DestDir: "{app}\licenses"; Flags: ignoreversion recursesubdirs
Source: "{#ProductRoot}\docs\维护手册.pdf"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#ProductRoot}\requirements.lock"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#ProductRoot}\LICENSE"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#ProductRoot}\THIRD_PARTY_NOTICES.md"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#ProductRoot}\launch.cmd"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#ProductRoot}\run.py"; DestDir: "{app}"; Flags: ignoreversion

[INI]
Filename: "{app}\installed.ini"; Section: "Application"; Key: "Version"; String: "{#ProductVersion}"; Flags: uninsdeleteentry uninsdeletesectionifempty

[Icons]
Name: "{userprograms}\物理实验助手"; Filename: "{app}\runtime\pythonw.exe"; Parameters: "-B -E -s -X utf8 ""{app}\run.py"""; WorkingDir: "{app}"

[Run]
Filename: "{sys}\icacls.exe"; Parameters: """{app}\runtime"" /grant *S-1-15-2-1:(OI)(CI)(RX)"; Flags: runhidden waituntilterminated
Filename: "{app}\runtime\pythonw.exe"; Parameters: "-B -E -s -X utf8 ""{app}\run.py"""; WorkingDir: "{app}"; Description: "启动物理实验助手"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
Type: files; Name: "{app}\installed.ini"
