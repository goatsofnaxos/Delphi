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

 ### Setting up the Bonsai environment
 To set up a Bonsai environment for running Delphi, use the terminal to clone the repo into a local folder:

 ``` 
 git clone https://github.com/goatsofnaxos/Delphi.git 
 ```

 Now navigate into Delphi/.bonsai and run the `Setup.ps1` script (on Windows right-click > Run with powershell). Once all dependencies have been restored Bonsai can be launched in a local environment from the `.exe` in this folder.

 ### Session Settings
 Experiment parameters are based on two data schemas `data-schema` and `rule-schema`. `data-schema` defines global experiment parameters such as animal ID information, logging paths, odor delivery times etc. `rule-schema` defines the experiment state machine with rules for how different states transition to each other. Implementations of these schemas are defined in `.yml` files provided by the user that are then loaded in Bonsai to set up experiment parameters.

 #### Working with data schemas in Visual Studio Code
 To create and edit schema implementations with full autcomplete and hinting, Visual Studio Code requires some extensions to be added:
 * json (ZainChen.json)
 * JSON Schema Validator (tberman.json-schema-validator)
 * YAML (redhat.vscode-yaml)

 #### Creating an experiment parameter file
 To create an experiment parameter file based on the `data-schema`, create a new `.yml` file and add a reference to the `data-schema` at the top of the file:

 ```
 %YAML 1.1
 ---
 # yaml-language-server: $schema=data-schema.json
 ```

The `data-schema` has three main groups of parameters to define: general metadata; camera properties; and line mappings, e.g.:

```
metadata:
  animalId: plimbo
  loggingRootPath: C:\Users\user\data\
  chargeTime: 0.2
  minimumPokeTime: 0.1
  maximumPokeTime: 10
  robocopyTimeInterval: 3600
  showHarpLeds: false
  maxVideoLength: 20
  minOdorDelivery: 0.1
  maxOdorDelivery: 8
  switchTime1: 0.01
  switchTime2: 0.02
  useVacuum: true

cameraProperties:
  imagingRate: 100
  preEventBufferFrames: 20
  postEventBufferFrames: 20
  bufferInterval: 1

lineMappings:
  odorMap:
    -   {name: "OdorA", line: 6}
    -   {name: "OdorB", line: 7}
    -   {name: "OdorC", line: 8}
    -   {name: "OdorD", line: 9}
  portLine: 0
  vacuumLine: 2
  auxLine: 1
```

#### Creating a rule file
To create a rule file based on the `rule-schema`, create a new `.yml` file and add a reference to the `rule-schema` at the top of the file.

```
%YAML 1.1
---
# yaml-language-server: $schema=rule-schema.json
```

Rule files have two main components:
* The rule alias which defines the human-readable name or description of the rule.
* State transition definitions which define which state to transition to after a given state has completed. The state names given here must match their names in the Bonsai workflow. For example:

```
ruleAlias: Rule1

stateDefinitions:
  -   {name: "OdorA", transitionsTo: "OdorB"}
  -   {name: "OdorB", transitionsTo: "OdorC"}
  -   {name: "OdorC", transitionsTo: "OdorA"}
  -   {name: "DefaultState", transitionsTo: "OdorA"}
```

It is important to always define a DefaultState transition rule, as this is the default fallback in case any states are not present.

### Running an experiment
To run an experiment, run the Bonsai application from the local environment and open DelphiMain.bonsai. Use the property grid to select the desired experiment parameter file and starting rule file. If the rule file is changed while the experiment is running, Bonsai will attempt to find a transition from its current state in the new rule file. If no transition is defined for that state in the new rule, or any of the states no longer exist in the new rule - Bonsai will transition to the default state.

### Analysis Prerequisites

The following need to be installed once on a fresh new system in order to analyze data acquired with Delphi:

 * [Visual Studio Code](https://code.visualstudio.com/) (recommended for editing code scripts and git commits)
 * [Python Extension for VS Code](https://marketplace.visualstudio.com/items?itemName=ms-python.python)
 * [Jupyter Extension for VS Code](https://marketplace.visualstudio.com/items?itemName=ms-toolsai.jupyter)
 * A conda distributiion such as [Miniconda](https://docs.anaconda.com/free/miniconda/index.html)

#### Create local Environment

 1. Open the Delphi folder in VS Code
 2. `Ctrl+Shift+P` in VS Code > Python: Create Environment
   * Select Conda
   * Select Python 3.11 kernel
 3. Ensure pip is upgraded:
    ```
    pip install --upgrade pip
    ```
 4. Clone aeon_mecha into a separate directory 
    ```
    git clone https://github.com/SainsburyWellcomeCentre/aeon_mecha.git 
    ```
 5. From the VS Code terminal, `cd` into the aeon_mecha directory and run:
    ```
    python -m pip install -e .
    ```
 6. From the terminal run `pip install -r requirements.txt`
 7. In VS Code open an analysis notebook (.ipynb) and click "Select kernel" > Python Environments
   * Select .conda local environment
 8. Analysis notebooks should now be able to run
