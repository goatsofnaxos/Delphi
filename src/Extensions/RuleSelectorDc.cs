using Bonsai;
using System;
using DcRuleSchema;
using System.ComponentModel;
using System.Reactive.Linq;
using System.IO;
using YamlDotNet.Core;
using YamlDotNet.Serialization;
using YamlDotNet.Serialization.NamingConventions;

public class RuleSelectorDc : Source<DelphiRuleDc>
{
    private DelphiRuleDc Rule;

    private string path = "rule1.yml";
    [Editor("Bonsai.Design.OpenFileNameEditor, Bonsai.Design", DesignTypes.UITypeEditor)]
    public string Path {
        get {
            return path;
        }
        set {
            path = value;

            DelphiRuleDc settings;
            using (var reader = new StreamReader(value)) {
                var parser = new MergingParser(new Parser(reader));

                var deserializer = new DeserializerBuilder()
                    .WithNamingConvention(CamelCaseNamingConvention.Instance)
                    .Build();
                
                settings = deserializer.Deserialize<DelphiRuleDc>(parser);
            }

            Rule = settings;
            OnValueChanged(Rule);
        }
    }

    event Action<DelphiRuleDc> ValueChanged;

    void OnValueChanged(DelphiRuleDc value)
    {
        if (ValueChanged != null) {
            ValueChanged.Invoke(value);
        }
    }

    public override IObservable<DelphiRuleDc> Generate()
    {
        return Observable
            .Defer(() => Observable.Return(Rule))
            .Concat(Observable.FromEvent<DelphiRuleDc>(
                handler => ValueChanged += handler,
                handler => ValueChanged -= handler));;
    }
}