# Check that two arguments are provided
if ($args.Count -ne 2) {
    Write-Host "Usage: .\move_data.ps1 <SourcePath> <DestinationPath>"
    exit 1
}

# Positional arguments
$Source = $args[0]
$Destination = $args[1]

# Full path to robocopy
$RobocopyExe = "$env:WINDIR\System32\robocopy.exe"

# Robocopy parameters
$RobocopyParams = @("/E", "/MOVE", "/J", "/R:2", "/W:30", "/NP")

# Build the command
$Command = @($RobocopyExe, $Source, $Destination) + $RobocopyParams

Write-Host "Running robocopy..."
Write-Host ($Command -join " ")

# Execute robocopy
$process = Start-Process -FilePath $RobocopyExe -ArgumentList $Command[1..($Command.Length-1)] -Wait -PassThru

# Robocopy exit codes: 0–7 = success
if ($process.ExitCode -le 7) {
    Write-Host "Robocopy completed successfully."
    exit 0
} else {
    Write-Host "Robocopy failed with exit code $($process.ExitCode)"
    exit $process.ExitCode
}