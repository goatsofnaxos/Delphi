namespace Extensions.Extensions
{
    partial class DeviceControl
    {
        /// <summary> 
        /// Required designer variable.
        /// </summary>
        private System.ComponentModel.IContainer components = null;

        /// <summary> 
        /// Clean up any resources being used.
        /// </summary>
        /// <param name="disposing">true if managed resources should be disposed; otherwise, false.</param>
        protected override void Dispose(bool disposing)
        {
            if (disposing && (components != null))
            {
                components.Dispose();
            }
            base.Dispose(disposing);
        }

        #region Component Designer generated code

        /// <summary> 
        /// Required method for Designer support - do not modify 
        /// the contents of this method with the code editor.
        /// </summary>
        private void InitializeComponent()
        {
            this.testLabel = new System.Windows.Forms.Label();
            this.SuspendLayout();
            // 
            // testLabel
            // 
            this.testLabel.AutoSize = true;
            this.testLabel.ForeColor = System.Drawing.SystemColors.Control;
            this.testLabel.Location = new System.Drawing.Point(178, 67);
            this.testLabel.Name = "testLabel";
            this.testLabel.Size = new System.Drawing.Size(146, 16);
            this.testLabel.TabIndex = 0;
            this.testLabel.Text = "Some default message";
            this.testLabel.TextAlign = System.Drawing.ContentAlignment.MiddleCenter;
            this.testLabel.Click += new System.EventHandler(this.testLabel_Click);
            // 
            // DeviceControl
            // 
            this.AutoScaleDimensions = new System.Drawing.SizeF(8F, 16F);
            this.AutoScaleMode = System.Windows.Forms.AutoScaleMode.Font;
            this.BackColor = System.Drawing.SystemColors.ControlDarkDark;
            this.Controls.Add(this.testLabel);
            this.ForeColor = System.Drawing.SystemColors.ControlDarkDark;
            this.Name = "DeviceControl";
            this.Size = new System.Drawing.Size(504, 150);
            this.Load += new System.EventHandler(this.DeviceControl_Load);
            this.ResumeLayout(false);
            this.PerformLayout();

        }

        #endregion

        private System.Windows.Forms.Label testLabel;
    }
}
