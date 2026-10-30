# this makes the script ask for company and title. Spaces ok.
param(
    [Parameter(Mandatory=$true)]
    [string]$Company,
    [Parameter(Mandatory=$true)]
    [string]$Title
)


function Get-ValidPaths {
    param(
        [string]$Company,
        [string]$Title
    )
    
    $rootResumePath = "..."
    $rootResumeSentPath   = "$rootResumePath\Sent"
    $rootResumeTemplatePath = "$rootResumePath"
    $resumeTemplate   = "$rootResumeTemplatePath\Dan Prince Resume template.dotx"
    $coverLetterTemplate = "$rootResumeTemplatePath\Dan Prince -company- Cover Letter template.dotx"
    $chatPromptTemplate       = "$rootResumeTemplatePath\chatPromptTemplate.txt"

    do {
        $newResumeFolderName = "$Company - $Title"
        $newResumeFolderPath = Join-Path -Path $rootResumeSentPath -ChildPath $newResumeFolderName

        $date = Get-Date -Format "MMMM d yyyy"
        $newResumeFileName = "Tyler Prince Resume - $Company - $Title - $date.docx"
        $coverLetterFileName = "Tyler Prince Cover Letter for $Company - $Title.docx"

        $newResumeFilePath = Join-Path -Path $newResumeFolderPath -ChildPath $newResumeFileName
        $coverLetterFilePath = Join-Path -Path $newResumeFolderPath -ChildPath $coverLetterFileName

        if ($newResumeFilePath.Length -lt 255 -and $coverLetterFilePath.Length -lt 255) {
            return @{
                NewResumeFolderPath = $newResumeFolderPath
                NewResumeFilePath    = $newResumeFilePath
                CoverLetterFilePath = $coverLetterFilePath
                ResumeTemplate = $resumeTemplate
                CoverLetterTemplate = $coverLetterTemplate
                ChatPromptTemplate = $chatPromptTemplate
            }
        }

        Write-Host "Error: One or both file paths exceed the 255-character limit. Please enter shorter values."

        $Company = Read-Host "Enter a shorter Company name"
        $Title = Read-Host "Enter a shorter Job Title"

    } while ($true)
}

# Get valid paths
$paths = Get-ValidPaths -Company $Company -Title $Title

# Create the job folder if it doesn't exist
if (!(Test-Path $paths.NewResumeFolderPath)) {
    New-Item -Path $paths.NewResumeFolderPath -ItemType Directory | Out-Null
} else {
    Write-Host "Directory already exists: $($paths.NewResumeFolderPath)"
}

function CreateWordDocumentFromTemplate {
    param(
        [string]$TemplatePath,
        [string]$SavePath
    )

    $wordApp = New-Object -ComObject Word.Application
    $wordApp.Visible = $true

    try {
        $doc = $wordApp.Documents.Add($TemplatePath)
        $doc.SaveAs([ref]$SavePath)
        $doc.Close()
    }
    catch {
        Write-Error "Error creating document from template: $_"
    }
    finally {
        $wordApp.Quit()
        [System.Runtime.Interopservices.Marshal]::ReleaseComObject($wordApp) | Out-Null
    }
}

# Create documents
CreateWordDocumentFromTemplate -TemplatePath $paths.ResumeTemplate -SavePath $paths.NewResumeFilePath
CreateWordDocumentFromTemplate -TemplatePath $paths.CoverLetterTemplate -SavePath $paths.CoverLetterFilePath

function Create-PromptFile {
    param(
        [string]$SourcePromptFile,
        [string]$DestinationFolder,
        [string]$Company,
        [string]$Title
    )
    $newPromptFileName = "$Company $Title prompt.txt"
    $newPromptFilePath = Join-Path -Path $DestinationFolder -ChildPath $newPromptFileName
    Copy-Item -Path $SourcePromptFile -Destination $newPromptFilePath -Force
    return $newPromptFilePath
}

# Create the prompt file
$newPromptFilePath = Create-PromptFile -SourcePromptFile $paths.ChatPromptTemplate -DestinationFolder $paths.NewResumeFolderPath -Company $Company -Title $Title

# Open the new text file with Notepad++
$notepadPPPath = "C:\Program Files\Notepad++\notepad++.exe"
if (Test-Path $notepadPPPath) {
    Start-Process -FilePath $notepadPPPath -ArgumentList "`"$newPromptFilePath`""
} else {
    Write-Error "Notepad++ not found at $notepadPPPath"
}

function Open-JobDocument {
    param([string]$Path)
    try {
        # Start Word and capture process object
        Start-Process -FilePath $Path -WindowStyle Minimized
        Write-Host "Opened document: $Path (PID: $($process.Id))"
    }
    catch {
        Write-Error "Failed to open path: $_"
    }
}


# Load the Minimize-Window function
. "$PSScriptRoot\Set-WindowState.ps1"

# Open Resume and minimize it
Open-JobDocument -Path $paths.NewResumeFilePath

Start-Sleep 2
# Open Cover Letter and minimize it
Open-JobDocument -Path $paths.CoverLetterFilePath
