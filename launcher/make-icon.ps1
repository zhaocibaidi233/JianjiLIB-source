Add-Type -AssemblyName System.Drawing

$size = 256
$bmp = New-Object System.Drawing.Bitmap $size, $size
$g = [System.Drawing.Graphics]::FromImage($bmp)
$g.SmoothingMode = [System.Drawing.Drawing2D.SmoothingMode]::AntiAlias

$rect = New-Object System.Drawing.Rectangle 0, 0, $size, $size
$brush = New-Object System.Drawing.Drawing2D.LinearGradientBrush(
    $rect,
    [System.Drawing.Color]::FromArgb(63, 138, 124),
    [System.Drawing.Color]::FromArgb(23, 94, 84),
    45.0)
$path = New-Object System.Drawing.Drawing2D.GraphicsPath
$r = 56
$d = $r * 2
$path.AddArc(0, 0, $d, $d, 180, 90)
$path.AddArc($size - $d, 0, $d, $d, 270, 90)
$path.AddArc($size - $d, $size - $d, $d, $d, 0, 90)
$path.AddArc(0, $size - $d, $d, $d, 90, 90)
$path.CloseFigure()
$g.FillPath($brush, $path)

$white = New-Object System.Drawing.SolidBrush ([System.Drawing.Color]::FromArgb(247, 245, 236))
$left = [System.Drawing.Point[]]@(
    (New-Object System.Drawing.Point 50, 82),
    (New-Object System.Drawing.Point 122, 100),
    (New-Object System.Drawing.Point 122, 190),
    (New-Object System.Drawing.Point 50, 172)
)
$right = [System.Drawing.Point[]]@(
    (New-Object System.Drawing.Point 206, 82),
    (New-Object System.Drawing.Point 134, 100),
    (New-Object System.Drawing.Point 134, 190),
    (New-Object System.Drawing.Point 206, 172)
)
$g.FillPolygon($white, $left)
$g.FillPolygon($white, $right)

$linePen = New-Object System.Drawing.Pen ([System.Drawing.Color]::FromArgb(200, 180, 122, 34)), 6
$g.DrawLine($linePen, 70, 130, 105, 139)
$g.DrawLine($linePen, 70, 152, 105, 161)
$g.DrawLine($linePen, 151, 139, 186, 130)
$g.DrawLine($linePen, 151, 161, 186, 152)

$spinePen = New-Object System.Drawing.Pen ([System.Drawing.Color]::FromArgb(235, 247, 245, 236)), 5
$g.DrawLine($spinePen, 128, 101, 128, 189)

$g.Dispose()
$icon = [System.Drawing.Icon]::FromHandle($bmp.GetHicon())
$out = Join-Path $PSScriptRoot 'jianji.ico'
$fs = [System.IO.File]::Create($out)
$icon.Save($fs)
$fs.Close()
$bmp.Dispose()
Write-Host "icon written: $out"
