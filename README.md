# Delphi

Delphi workflow


### Acquisition Prerequisites

The following need to be installed once on a fresh new system in order to bootstrap the Bonsai environment correctly:

 * Windows 10 or greater
 * [Visual Studio Code](https://code.visualstudio.com/) (recommended for editing code scripts and git commits)
 * [.NET 8.0 SDK](https://dotnet.microsoft.com/en-us/download/dotnet/8.0)
 * [.NET Framework 4.7.2 Developer Pack](https://dotnet.microsoft.com/download/dotnet-framework/thank-you/net472-developer-pack-offline-installer) (required for intellisense when editing code scripts)
 * [Git for Windows](https://gitforwindows.org/) (recommended for cloning and manipulating this repository)
 * [Visual C++ Redistributable for Visual Studio 2012](https://www.microsoft.com/en-us/download/details.aspx?id=30679) (native dependency for OpenCV)
 * [FTDI CDM Driver 2.12.28](https://www.ftdichip.com/Drivers/CDM/CDM21228_Setup.zip) (serial port drivers for HARP devices)
 * [Spinnaker SDK 1.29.0.5](https://www.flir.co.uk/support/products/spinnaker-sdk/#Downloads) (device drivers for FLIR cameras, sign in required, look in the archived stable versions for 1.29.0.5 64-bit full install)

### Analysis Prerequisites

The following need to be installed once on a fresh new system in order to analyze data acquired with Delphi:

 * [Visual Studio Code](https://code.visualstudio.com/) (recommended for editing code scripts and git commits)
 * [Python Extension for VS Code](https://marketplace.visualstudio.com/items?itemName=ms-python.python)
 * [Python 3.11.7](https://www.python.org/downloads/release/python-3117/)

#### Create local Environment

 1. `Ctrl+Shift+P` in VS Code > Python: Create Environment
   * Select .venv
   * Select Python 3.11 kernel
 2. From the terminal run `pip install -r requirements.txt`
