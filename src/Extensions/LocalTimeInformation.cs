using Bonsai;
using System;
using System.ComponentModel;
using System.Collections.Generic;
using System.Linq;
using System.Reactive.Linq;

[Combinator]
[Description("")]
[WorkflowElementCategory(ElementCategory.Source)]
public class LocalTimeInformation
{
    public IObservable<Tuple<DateTime, TimeZone>> Process()
    {
        return Observable.Return(new Tuple<DateTime, TimeZone>(DateTime.Now, TimeZone.CurrentTimeZone));
    }
}
