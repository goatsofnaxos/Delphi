
using System;
using System.Collections.Generic;
using System.Linq;
using RuleSchema;

public static class Utils
{
    static Random rnd = new Random(DateTime.Now.Millisecond);

    public static string GetUniformStateProbability(List<string> stateProbabilities)
    {
        return stateProbabilities.OrderBy(_ => Guid.NewGuid()).ToList()[0];
    }
}