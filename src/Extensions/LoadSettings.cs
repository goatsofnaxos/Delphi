using Bonsai;
using System;
using System.ComponentModel;
using System.Collections.Generic;
using System.Linq;
using System.Reactive.Linq;
using DataSchema;
using System.IO;
using YamlDotNet.Core;
using YamlDotNet.Serialization;
using YamlDotNet.Serialization.NamingConventions;

[Combinator]
[Description("")]
[WorkflowElementCategory(ElementCategory.Source)]
public class LoadSettings
{
    [Editor("Bonsai.Design.OpenFileNameEditor, Bonsai.Design", DesignTypes.UITypeEditor)]
    public string Path { get; set;}

    public IObservable<DelphiSession> Process()
    {
        return Observable.Defer(() =>
        {
            DelphiSession settings;
            using (var reader = new StreamReader(Path))
            {
                var parser = new MergingParser(new Parser(reader));

                var deserializer = new DeserializerBuilder()
                    .WithNamingConvention(CamelCaseNamingConvention.Instance)
                    .Build();
                settings =  deserializer.Deserialize<DelphiSession>(parser);
            }
            return Observable.Return(settings);
        });
    }
}