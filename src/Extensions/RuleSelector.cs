using Bonsai;
using System;
using RuleSchema;
using System.ComponentModel;
using System.Reactive.Linq;
using System.IO;
using YamlDotNet.Core;
using YamlDotNet.Serialization;
using YamlDotNet.Serialization.NamingConventions;

public class RuleSelector : Source<DelphiRule>
{
    private DelphiRule Rule;

    private string path;
    [Editor("Bonsai.Design.OpenFileNameEditor, Bonsai.Design", DesignTypes.UITypeEditor)]
    public string Path {
        get {
            return path;
        }
        set {
            path = value;

            DelphiRule settings;
            using (var reader = new StreamReader(value)) {
                var parser = new MergingParser(new Parser(reader));

                var deserializer = new DeserializerBuilder()
                    .WithNamingConvention(CamelCaseNamingConvention.Instance)
                    .Build();
                
                settings = deserializer.Deserialize<DelphiRule>(parser);
            }

            Rule = settings;
            OnValueChanged(Rule);
        }
    }

    event Action<DelphiRule> ValueChanged;

    void OnValueChanged(DelphiRule value)
    {
        ValueChanged.Invoke(value);
    }

    public override IObservable<DelphiRule> Generate()
    {
        return Observable
            .Defer(() => Observable.Return(Rule))
            .Concat(Observable.FromEvent<DelphiRule>(
                handler => ValueChanged += handler,
                handler => ValueChanged -= handler));;
    }
}