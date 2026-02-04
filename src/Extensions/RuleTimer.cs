using Bonsai;
using System;
using System.ComponentModel;
using System.Collections.Generic;
using System.Linq;
using System.Reactive.Linq;
using System.IO;

[Combinator]
[Description("Generates a sequence of rule schedules from a pre-programmed list.")]
[WorkflowElementCategory(ElementCategory.Source)]
public class RuleTimer
{
    public RuleTimer()
    {
        DueTimes = new List<RuleSchedule>();
    }

    [Description("The datetime at which the timer will fire.")]
    public List<RuleSchedule> DueTimes { get; set; }

    public IObservable<string> Process()
    {
        return DueTimes.Where(schedule => schedule.DueTime > DateTime.Now).Select(schedule =>
            Observable.Timer(schedule.DueTime)
                      .Select(_ => File.ReadAllText(schedule.RuleSchema)))
                      .Merge();
    }
}

public class RuleSchedule
{
    public DateTime DueTime { get; set; }

    [Editor(DesignTypes.OpenFileNameEditor, DesignTypes.UITypeEditor)]
    public string RuleSchema { get; set; }

    public override string ToString()
    {
        return RuleSchema + "@" + DueTime;
    }
}
