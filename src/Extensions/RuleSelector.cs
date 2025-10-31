using Bonsai;
using System;
using RuleSchema;
using System.ComponentModel;
using System.Reactive.Linq;
using System.IO;
using YamlDotNet.Core;
using YamlDotNet.Serialization;
using YamlDotNet.Serialization.NamingConventions;

public class RuleSelector : Source<string>
{
    private string Rule;

    private string path = "";
    [Editor("Bonsai.Design.OpenFileNameEditor, Bonsai.Design", DesignTypes.UITypeEditor)]
    public string Path {
        get {
            return path;
        }
        set {
            path = value;

            var reader = new StreamReader(value);
            Rule = reader.ReadToEnd();

            OnValueChanged(Rule);
        }
    }

    event Action<string> ValueChanged;

    void OnValueChanged(string value)
    {
        if (ValueChanged != null) {
            ValueChanged.Invoke(value);
        }
    }

    public override IObservable<string> Generate()
    {
        return Observable
            .Defer(() => Observable.Return(Rule))
            .Concat(Observable.FromEvent<string>(
                handler => ValueChanged += handler,
                handler => ValueChanged -= handler));;
    }
}